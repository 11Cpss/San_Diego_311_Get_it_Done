# Lesson 8: MLflow, daily scoring and model monitoring

> Code paths are relative to the repo root.

## What you'll build and why it matters

You will write the two decisions that keep the model honest after launch: `psi()`, the drift
number behind the CloudWatch `MaxPSI` alarm, and `promotion_decision()`, the champion/challenger
rule that decides whether a newly trained model serves tomorrow's predictions. Every alias move
in the MLflow registry and every drift alarm then comes from your code, and "how do you monitor a
model you can't grade for a month?" becomes a question you can answer from your own repo.

## Concept

Lessons 1–3 built a model offline. This lesson is about using it every day and
noticing when it stops being good.

- **Daily scoring.** Each morning, new requests arrive. For each one we build the
  same features the model was trained on, using only what was known at that moment,
  and store a predicted bucket plus four probabilities in `sd311.ml.predictions`.
- **MLflow** keeps track of training runs (parameters, metrics, figures, files)
  and the **model registry** holds numbered model versions. On Databricks the
  registry is Unity Catalog, so the model's full name is `sd311.ml.closure_bucket`.
- **Aliases** are movable name tags on versions. `@champion` is the version the
  daily job uses. `@challenger` is a newer version that didn't earn promotion.
  Scoring code always loads `models:/sd311.ml.closure_bucket@champion`, so you
  promote or roll back by moving a tag. No code changes, no redeploy.
- **Live metrics.** We can only grade a prediction once its true bucket is
  *final*, and that can take up to 31 days (a request is "31+" once it has been
  open 31 days). So live macro-F1 always describes predictions made weeks ago.
- **Drift (PSI).** We can see right away whether the *inputs* look different
  from training. The Population Stability Index compares two category mixes:
  `PSI = Σ (actual − expected) · ln(actual / expected)`. 0 means identical.
  Rule of thumb: < 0.1 stable, 0.1–0.25 moderate, > 0.25 big shift.

## How it fits the pipeline

```
daily job (databricks/databricks.yml)
  features ─► score (loads models:/sd311.ml.closure_bucket@champion) ─► evaluate_and_summarize
                                                                           ├─ live_metrics(...)
                                                                           └─ drift_report(...) ─► psi()  (one per column)
                                                                                 └─► run_summary.json drift.max_psi
08:00 PT  Lambda sd311-monitor (src/sd311/monitor.py) ─► CloudWatch SD311/MaxPSI ─► alarm (infra/main.tf) ─► SNS email

train job (databricks/tasks/train.py, on demand / scheduled)
  backtest folds (lesson 3) ─► register new version ─► registry.assign_alias ─► promotion_decision()
                                                         └─► alias @champion or @challenger + "promotion_reason" tag
```

- `psi` runs every morning inside the `evaluate_and_summarize` task (serverless, `ml`
  environment). Its largest value becomes `drift.max_psi` in `run_summary.json`; the monitor
  Lambda publishes it and the `max-psi-high` alarm pages you (`docs/runbook.md#max-psi-high`).
- `promotion_decision` runs once per training run. Its `alias` moves the registry tag the daily
  `score` task loads, so it decides which model serves; its `reason` is stored on the version.

## Code walkthrough

Files to open: `ml/sd311_core/serving.py`, `databricks/tasks/score.py`,
`databricks/tasks/train.py`, `ml/sd311_core/registry.py`, `ml/sd311_core/monitoring.py`,
`databricks/tasks/evaluate_and_summarize.py`.

**Glue vs logic.** Each Databricks task is about 60 lines: read tables with Spark,
call one `sd311_core` function, write the result. All the logic is plain pandas in
`ml/sd311_core/` and has unit tests. You can run those tests on a laptop in seconds.

**Bounded history** (`serving.py`). Features for day *t* need only:

```python
REQUESTED_LOOKBACK_DAYS = 125   # 90-day mix window + 31-day "open and old" rule + slack
CLOSED_LOOKBACK_DAYS = 95       # labels finalised by a closure in the mix window

def history_filter_sql(snapshot_date):   # simplified; the real one uses the constants
    return (f"(CAST(date_requested AS DATE) >= DATE_SUB(DATE '{d}', 125)"
            f" OR date_closed IS NULL"
            f" OR CAST(date_closed AS DATE) >= DATE_SUB(DATE '{d}', 95))")
```

The backlog is "submitted before t minus closed before t". A request that was
submitted *and* closed earlier adds +1 and −1, so we can drop it safely. We must
keep everything still open. `test_history_window_gives_same_features_as_full_history`
proves this on synthetic data. If you shorten the window to 40/10 days, the test fails
(we checked).

**First-seen values** (`build_daily_features`). New requests come from
`silver.requests_first_seen` (fields as they looked on day 1). Their latest-state
copies are removed from the history first, so a later edit can't leak in
(`test_new_rows_use_first_seen_values`).

**Scoring** (`score.py`):

```python
mv = get_alias_version(client, name, "champion")      # None -> skip, job still succeeds
model = mlflow.catboost.load_model(f"models:/{name}@champion")
preds = score_frame(model, feats, snap, model_version=mv.version)
# columns: service_request_id, snapshot_date, model_version, pred_bucket, p_0_2..p_31_plus
```

**Training and promotion** (`train.py`, `registry.py`). `train.py` runs the backtest folds,
logs the model and registers a new version, then hands the fold results to the registry helper
(given):

```python
def assign_alias(client, model_name, version, fold_results):
    decision = promotion_decision(fold_results, champion_mean_log_loss(client, model_name))   # yours
    client.set_registered_model_alias(model_name, decision["alias"], str(version))
    client.set_model_version_tag(model_name, str(version), "promotion_reason", decision["reason"])
```

`champion_mean_log_loss` reads the current champion's `backtest_mean_log_loss` from its MLflow
run (None if there is no champion yet). `ml/tests/test_registry.py` runs the full sequence
(champion → challenger → still challenger → new champion) against a real local MLflow file
store, with your `promotion_decision` inside.

**Monitoring** (`evaluate_and_summarize.py`, `monitoring.py`):
- `live_metrics` re-derives labels with `sd311_core.labels.make_labels(as_of=today)`,
  keeps labels that became final in the last 28 days, and grades the *first*
  prediction for each request.
- `drift_report` (given) compares the last 7 days' `service_name` / `case_origin` shares
  (`category_shares`) with `reference_distributions.json`, which the train job logged next to the
  model, by calling your `psi` once per column:

```python
by = {c: round(psi(reference[c], category_shares(recent[c])), 4)
      for c in columns if c in reference and c in recent.columns}
return {"max_psi": max(by.values()), "psi_by_feature": by}
```
- `freshness_hours` treats city timestamps as America/Los_Angeles wall-clock time
  and converts them to UTC before subtracting. The old code compared them directly
  with UTC and was off by 7–8 hours.

### A real bug we found: train/serve skew

"Same feature code in training and serving" (first tradeoff below) removes one kind of
skew, but not all of it. The *inputs* to that code can still differ. We found two
such bugs while wiring the model into the daily job, and both are now fixed.

**How we found them.** Pick one real day (2025-11-15) and build its features
twice: (a) read the rows for that day from the offline training table
`ml/data/dataset.parquet`, and (b) run the daily path (`build_daily_features`)
on only what the pipeline would have known that morning. Then compare column by
column. In a correct pipeline, every number matches. Calendar, workload and
backlog features did match. The five `mix90_*` columns did not (bug 1).
Bug 2 showed up when we asked the same question about the *inputs*: for each
feature, where does the daily job get its value from? For `desc_len` the answer
was "nowhere": the silver tables had no description, so the code filled 0.

**Bug 1: bucket-mix finality.** `mix90_*` is the share of each bucket among a
service's requests whose label became *final* in the last 90 days. `make_labels`
said a slow request that *later* closed became final at its close date. But "31+"
is certain 31 days after submission, because the request can no longer close
faster. The daily job uses `as_of = today`, so it already counts such a request at
day 31 (it is still open and old). The offline dataset uses `as_of = 2025-12-31`,
when the request has closed, so it counted the request only from the close date.
Result on the real data: on 2025-11-15, 546 of 551 new requests had different
`mix90_*` values, and `mix90_31p` was on average 0.029 higher in serving than in
training. On 2025-03-03 it was all 1,313 requests, +0.022. The model was trained on
one definition and scored with another.

*Fix* (`ml/sd311_core/labels.py`, the finality rule you wrote in lesson 2): a label becomes final at
`min(date_closed + 1 day, date_requested + 31 days)` for "31+" requests (which is
always `date_requested + 31 days`), and at the end of its close day
(`date_closed + 1 day`) for the others. Now `label_final_at` no longer depends on
`as_of` once a label is final. Because this changes the training features, we
rebuilt the dataset, re-ran the backtest and refitted `model.cbm`
(see `ml/reports/RESULTS.md`). The pooled backtest barely moved: model macro-F1
0.532 → 0.531, log-loss 0.915 → 0.917. The baseline rose a little (0.424 → 0.432),
because it uses the same 90-day mix. Small changes like this are expected and fine.
The point of the fix is that the numbers we report now describe the model that
actually runs.

**Bug 2: `desc_len` was 0 at serving.** The model uses the description length.
The training files have it (`make_slim.py` stores `len(public_description)`), but
the silver tables did not carry the description at all. So `prepare_requests`
filled `desc_len = 0`, and every scored request looked like "no description"
(`has_description = 0`). In training that is true for only 13% of all rows (20%
in the final model's 2024-01..2025-10 window). No test failed, because the daily-path tests used synthetic frames without the column.

*Fix*: compute `desc_len` where the data enters silver, from bronze's
`public_description`: `COALESCE(LENGTH(public_description), 0)` in
`databricks/tasks/snapshots.py` (both `requests_first_seen` and
`requests_current`) and in dbt staging → `requests_clean`. Serving now reads the
*first-seen* `desc_len`. `prepare_requests` computes the length itself if it gets
only the text, and warns if it gets neither.

**Tests that would have caught them** (now in the suite):
- `ml/tests/test_serving.py::test_offline_training_features_equal_daily_serving_features`.
  This test builds a small world in which slow requests close long after day 31.
  It builds offline features with `as_of = 2025-12-31` and daily features from
  "what was known on day t" for three days, then asserts every numeric feature
  is equal. With the old `labels.py` it fails on all three days. The older test
  used `as_of = t` for both sides, so it compared the bug with itself.
- `test_desc_len_from_first_seen_reaches_features` and
  `databricks/tests/test_snapshots.py::test_desc_len_is_derived_from_public_description`.

**The lesson:** sharing the same code between training and serving is not enough.
Add a test that compares the offline and online paths on the same day, and run it on
real data as well (`python ml/analysis/train_serve_skew.py 2025-11-15 2025-03-03`).
After the fix, that comparison finds 0 differing rows on both days (all numeric
features equal).

## Your task

### 1. `psi(reference, actual, eps=1e-4)` in `ml/sd311_core/monitoring.py`

- **Returns** a float: the sum over categories of `(a - e) * ln(a / e)`.
- **Rules:** pool every category of `actual` that is not in `reference` into `OTHER`
  (`"__other__"`); sum over the union of categories, a missing side counting as 0; floor both
  shares at `eps` before the formula; don't modify the inputs.
- **Worked example** (in the docstring): `{a: .5, b: .5}` vs `{a: .8, b: .2}` → 0.416.
- **Hints:** docstring hints 1–2.

### 2. `promotion_decision(fold_results, champion_mean_log_loss)` in `ml/sd311_core/monitoring.py`

- **Returns** `{"alias", "mean_log_loss", "beats_baseline_every_fold", "min_f1_margin", "reason"}`.
- **Rules:** `"champion"` only if model macro-F1 > baseline macro-F1 in *every* fold (a tie is not
  a win) **and** mean log-loss < the champion's (or there is no champion; equal is not better).
  The reason names the worst fold as `fold {i}` (0-based, first on a tie) when rule 1 fails and
  contains `log-loss` when rule 2 fails; both if both fail. No folds → the fixed "no backtest
  folds" dict in the docstring.
- **Hints:** docstring hints 1–2.

### Check

```bash
python scripts/lessons.py check 8
```

This runs the 20 tests marked `lesson8`: hand-computed PSI values (floor, pooling, custom eps,
symmetry), `drift_report` on top of your `psi`, the promotion rule's cases, and the MLflow
registry sequence in `test_registry.py` (needs `mlflow`, installed by `pip install -e ".[ml]"`).

If stuck: `python scripts/lessons.py diff 8`, or `solve 8 --yes` (your version is backed up).

## Own it

1. In 3–5 sentences: with `service_name` shares `{Pothole: 0.5, Graffiti: 0.5}` in training,
   what weekly mix pushes PSI over 0.25? Check your guess with `psi()` in a Python shell. Which
   real events (a storm, an app release that changes `case_origin`, a new service category)
   would fire the `MaxPSI` alarm, and which of them should *not* lead to a retrain?
2. Would drift monitoring have caught bug 2 (`desc_len = 0` at serving)? `DRIFT_COLUMNS` only
   watches `service_name` and `case_origin`. Compute by hand or in a shell the PSI between the
   training shares of `has_description` (about `{1: 0.8, 0: 0.2}`) and the buggy serving shares
   `{0: 1.0}`. Would the 0.25 alarm have fired? Then argue in two sentences why a direct
   offline-vs-online feature comparison is still the better check.
3. Explain why the promotion rule requires beating the baseline in *every* fold instead of on
   average, and what the rule cannot protect against (hint: leakage).
4. **Change one thing and predict.** Set `PSI_ALARM_THRESHOLD = 0.5` in `monitoring.py`. Before
   running, predict which test fails and why (the hand-computed shift has PSI 0.416). Then set it to
   0.1: does anything fail? Run `python scripts/lessons.py check 8` each time, and say what that
   tells you about what the test really checks. Put it back to 0.25.

## Tradeoffs

- **pandas inside the task vs Spark-native features.** One scoring day needs about
  300k history rows (measured on the real data for 2025-11-15: 319,307 of
  2.9M). `toPandas()` handles that easily, and we reuse the exact feature code
  that was backtested. That means one implementation and no train/serve drift in
  the code. The cost is memory on the driver. At 10× the data, rewrite the window
  counts as Spark window functions, and test them against the pandas version.
- **Champion/challenger.** The rule is simple and conservative. A new model must
  beat the baseline in *every* fold (not just on average) and have lower log-loss
  than the champion. Weakness: the champion's log-loss came from its own backtest,
  run on older folds. A fairer comparison re-scores both on the same folds.
  Log-loss also rewards calibration, which matters because we publish probabilities.
- **PSI thresholds.** 0.1/0.25 are conventions, not laws. PSI grows with the number
  of categories (about 40 services here) and is noisy on small samples. That's why we
  use 7 days of requests, not 1. A storm week *should* move the service mix, so treat
  an alarm as "look", not "retrain". Terraform's `psi_alarm_threshold` is what actually
  pages someone. `PSI_ALARM_THRESHOLD` in code is only documentation and tests.
- **Why live metrics lag about 31 days.** A request submitted today might be 31+.
  We only know that for sure on day 31. If we graded earlier, we'd only grade the
  requests that closed fast, and the metric would look great while the hard cases
  were still missing (survivorship bias). The 28-day window of *final* labels
  therefore covers predictions made about 1–2 months ago. For quicker warning, use drift.

## Interview questions

1. Why load `models:/name@champion` instead of a version number?
2. Your live F1 dropped this week. Is the model getting worse right now?
3. What is PSI and what are its weaknesses?
4. Why compute features in pandas inside a Spark job?
5. What stops a model trained on leaked future data from becoming champion?
6. Why pool unseen categories into one `__other__` bucket instead of ignoring them?

## Time

| part | working | minutes |
|---|---|---|
| Read | 2,520 words ÷ 150 wpm ≈ 17, + 6 files opened (serving.py, score.py, train.py, registry.py, monitoring.py, evaluate_and_summarize.py) × 5 = 30 | 47 |
| Build | `psi`: small (pool, floor, one sum) 15; `promotion_decision`: medium (two rules, tie handling, reason text, empty input) 30 | 45 |
| Check and explain | 2 functions × 10 + "Own it" 10 | 30 |
| **Total** | 47 + 45 + 30 = 122 | **120** |

## Answers

1. Promotion and rollback become a registry change (move the alias). Scoring code and deployed
   jobs stay the same, and every prediction row records which version made it.
2. Not necessarily. Labels lag up to 31 days, so it reflects predictions from 1–2 months ago.
   Check drift (PSI) and data quality for what's happening now.
3. A symmetric, KL-like distance between two binned distributions. It is sensitive to binning, to
   rare categories (needs a floor) and to small samples, and it says nothing about whether the
   model's *accuracy* changed.
4. The data for one day is small. Reusing the exact backtested feature code removes a whole class
   of train/serve skew bugs. The limit is driver memory; switch to Spark when the data outgrows it.
5. Time-ordered backtest folds with a 31-day embargo, labels that must be final at the cutoff, the
   must-beat-baseline-in-every-fold rule, and features built only from first-seen values. The rule
   can't catch leakage on its own; the time-aware evaluation is what makes its numbers trustworthy.
6. A brand-new service is exactly the drift you want to see. Ignoring unseen categories would
   hide it; pooling them keeps the comparison to one extra bin, so many tiny new categories don't
   each add a huge floored term.
