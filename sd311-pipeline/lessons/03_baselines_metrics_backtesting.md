# Lesson 3: Baselines, metrics and backtesting

> Code paths are relative to the repo root. The model code lives in `ml/`; ADRs are in `docs/adr/`.

## What you'll build and why it matters

You will write `make_fold`, which sets the train / validation / embargo / test dates of one
backtest fold, and `fit_baseline`, the per-service rule the model has to beat. Together they
decide what "the model is better" means. After this lesson every number in the backtest table of
`ml/reports/RESULTS.md` is produced by your code, and you will rerun one fold to prove it.

## Concept

**Baseline.** Before you trust a model, ask what a simple rule would score. Our baseline is the
kind of rule a dispatcher could keep in a spreadsheet: *for each service, predict whichever bucket
was most common among its requests whose outcome became known in the last 90 days.* If the model
can't clearly beat that, it isn't worth running. Note "became known": the baseline must obey the
same as-of rule as the features, so it is keyed on `label_final_at` (lesson 2), not on
`date_requested`.

**Metrics.** With four unbalanced classes (about 47% of requests are `0-2`), accuracy rewards
always guessing "fast". We report:
- **macro-F1**: the F1 score of each class, averaged with equal weight, so the rare middle buckets count;
- **multiclass log-loss**: scores the probabilities, not just the top guess, which matters if the
  city shows "70% chance this is done within a week";
- **per-class recall** and the **confusion matrix**, which show *where* the errors are.

**Backtesting.** One train/test split gives you one number from one season. A **rolling-origin**
backtest repeats the process the way it would run in production: at each origin T, train on the
past 24 months, wait out the 31-day label embargo, then score the next 3 months. Five origins give
five honest numbers and show whether the gain is stable.

```
|<-------- train (24 months) -------->|<- valid 30d ->|<- embargo 31d ->|<-- test (3 months) -->|
T-24m                              E-30d              E = T-31d         T                     T+3m
```

## How it fits the pipeline

- **Offline backtest:** `ml/scripts/run_backtest.py` loops over 5 origins:
  `run_fold(data, make_fold(origin))`. `run_fold` filters the rows with the fold's dates, trains
  CatBoost, calls `fit_baseline(data, fold.test_start)` and scores both on the same test rows.
  Output: `ml/reports/backtest.json` → `make_figures.py` → `RESULTS.md`.
- **Final model:** `ml/scripts/fit_final.py` uses `make_fold(2026-01-01)` to pick the newest
  train/valid window.
- **Databricks `train` job** (`databricks/tasks/train.py`): `quarterly_origins(as_of, 4)` →
  `make_fold` → `run_fold` for each, then the same `make_fold(as_of + 1 day)` for the final model.
  The per-fold model-vs-baseline results feed the promotion rule you write in lesson 8.

## Code walkthrough

**1. `ml/sd311_core/backtest.py`** (open it). Read the module docstring and the `Fold` dataclass
(all windows half-open). Then the given code that uses your fold:

```python
def run_fold(data, fold, max_train_rows=300_000, params=None, seed=0):
    train = _between(data, fold.train_start, fold.valid_start)    # rows WITH a final label only
    valid = _between(data, fold.valid_start, fold.valid_end)
    test = _between(data, fold.test_start, fold.test_end)
    ...
    base = fit_baseline(data, fold.test_start)
    b_pred, b_proba = predict_baseline(base, test["service_name"])
```

`quarterly_origins` (also given) picks origins whose whole test window has final labels.

**2. `ml/sd311_core/baseline.py`** (open it). `predict_baseline` is given: it looks each service
up in `by_service` and falls back to `global`, then takes the argmax as the prediction.

```python
proba = np.vstack([model["by_service"].get(s, model["global"]) for s in services.astype(str)])
```

So your `fit_baseline` must return exactly that dict shape.

**3. `ml/sd311_core/metrics.py`** (open it). One `evaluate` function for every metric. Note the
log-loss comment: sklearn assumes probability columns in *sorted* label order, and sorted strings
put `"31+"` before `"8-30"`. We pass integer codes to avoid a silent column mix-up (this bug did
happen during development and only showed up as a warning).

**4. `ml/scripts/run_backtest.py`** (open it): 30 lines, the 5 origins (test months 2024-07 to 2025-09).

**5. `ml/reports/RESULTS.md`** (open it). The numbers you will reproduce:

| test quarter | model macro-F1 | baseline macro-F1 | model log-loss | baseline log-loss |
|---|---|---|---|---|
| 2024-07 | 0.550 | 0.493 | 0.924 | 1.058 |
| 2024-10 | 0.508 | 0.386 | 0.900 | 1.069 |
| 2025-01 | 0.529 | 0.350 | 0.972 | 1.127 |
| 2025-04 | 0.528 | 0.380 | 0.881 | 1.022 |
| 2025-07 | 0.519 | 0.395 | 0.905 | 1.051 |
| **all 456,265 test requests** | **0.531** | **0.432** | **0.917** | **1.065** |

How to read it honestly:
- The model beats the baseline in **every** fold, but the first fold's margin is the smallest (+0.06).
- The gain is on the slower buckets: recall for `3-7` rises from 0.18 to 0.35 and for `31+`
  from 0.46 to 0.65. For `0-2` the baseline is better (0.79 vs 0.76).
- The middle buckets are still hard: only 35% of true `3-7` and 37% of true `8-30` requests are
  predicted correctly.
- The pooled baseline macro-F1 (0.432) is higher than four of the five folds because F1 is not an
  average over folds. Report both.

## Your task

### 1. `make_fold(test_start, train_months=24, test_months=3)` in `ml/sd311_core/backtest.py`

- **Returns** a `Fold`: `train_start = T - train_months months`, `valid_end = T - 31 days`,
  `valid_start = valid_end - 30 days`, `test_start = T`, `test_end = T + test_months months`.
- **Rules:** accept a string or a Timestamp; calendar months via `pd.DateOffset`, days via
  `pd.Timedelta` (the tests include a leap-year February and a month-end origin).
- **Hints:** docstring hints 1–2.

### 2. `fit_baseline(data, period_start, window_days=90, min_count=30)` in `ml/sd311_core/baseline.py`

- **Returns** `{"global": array(4), "by_service": {str: array(4)}, "period_start": "YYYY-MM-DD"}`,
  arrays in `BUCKETS` order, each summing to 1.
- **Rules:** use rows with `T - window_days < label_final_at <= T` and a bucket; `global` over all
  of them; a service gets its own mix only with at least `min_count` rows; keys are strings;
  absent buckets get 0.0.
- **Hints:** docstring hints 1–3 (`pd.crosstab` + `reindex` does most of it).

### Check

```bash
python scripts/lessons.py check 3
```

This runs the 13 hand-computed tests in `ml/tests/test_backtest_baseline.py`, then
`python3 ml/scripts/verify_real_data.py fold 2025-07-01` (about 20 seconds): it builds the
2025-07 fold with **your** `make_fold`, fits **your** baseline on the real `dataset.parquet` and
compares the fold dates, `n_valid`, `n_test` and the baseline macro-F1 / accuracy / log-loss with
`reports/backtest.json`. (It prints `SKIPPED` if `ml/data/dataset.parquet` is missing; build it
once, see lesson 2.)

### Rerun one fold with your code

The check skips the CatBoost training to stay fast. Run the whole fold once (about 8–9 minutes
on 2 CPUs):

```bash
python3 ml/scripts/verify_real_data.py fold 2025-07-01 --with-model
```

Expected, matching the 2025-07 row of `RESULTS.md`:

```
ok   baseline macro_f1  yours 0.3954  report 0.3954
ok   baseline log_loss  yours 1.0509  report 1.0509
ok   model    macro_f1  yours 0.5190  report 0.5190
ok   model    log_loss  yours 0.9049  report 0.9049
```

(Verified on the build machine: CatBoost stops at the same iteration, 459, and every number
matches.) Matching numbers mean: *these results are produced by my code.* That is the sentence
to be able to say in an interview.

If stuck: `python scripts/lessons.py diff 3`, or `solve 3 --yes` (your version is backed up).

## Own it

1. Draw the fold diagram from memory for T = 2025-04-01 with the real dates (check against
   `reports/backtest.json`). Why is the gap exactly 31 days and not 30 or 60?
2. Explain why `fit_baseline` filters on `label_final_at` and not on `date_requested`. Which
   requests would the wrong filter wrongly include?
3. Why do we report macro-F1 *and* log-loss, and when would they disagree?
4. **Change one thing and predict.** In your `fit_baseline`, filter the 90-day window on
   `date_requested` instead of `label_final_at`. Before running, predict: which tests fail, and
   will the real 2025-07 baseline macro-F1 go up or down? Run `python scripts/lessons.py check 3`,
   compare with your prediction, then undo the change.

## Tradeoffs

- **Quarterly vs. monthly origins:** monthly would be closer to production (retrain often) but
  costs 3x the compute. With quarterly origins, the last test month can be about 5 months after
  the newest training row (train ends 61 days before T).
- **Sampling 300k training rows** keeps each fold to a few minutes on 2 CPUs, but a full-data
  model might be a little better.
- **No hyperparameter search:** fixed CatBoost settings with early stopping on the validation
  month. A light search is the next step; it must never look at test months.
- **Baseline strength:** a 90-day per-service mix is a fair "what a person would do" baseline. A
  stronger one (service × neighborhood, service × origin) would shrink the gap. Worth adding
  before making big claims.

## Interview questions

1. Why macro-F1 instead of accuracy here?
2. What does log-loss tell you that F1 does not?
3. What is a rolling-origin backtest, and why use one?
4. Why is there a gap (embargo) between train and test?
5. Your model improves macro-F1 from 0.43 to 0.53. Is that good?
6. How do you know the numbers in your results table came from the code in your repo?

## Time

| part | working | minutes |
|---|---|---|
| Read | 1,570 words ÷ 150 wpm ≈ 11, + 5 files opened (backtest.py, baseline.py, metrics.py, run_backtest.py, RESULTS.md) × 5 = 25 | 36 |
| Build | `make_fold`: small (5 date expressions, month vs day offsets) 15; `fit_baseline`: medium (window filter, crosstab, fallback, string keys) 30 | 45 |
| Check and explain | 2 functions × 10 + "Own it" 10 (the optional 9-minute full-fold run is waiting time, not counted) | 30 |
| **Total** | 36 + 45 + 30 = 111 | **110** |

## Answers

1. Classes are unbalanced. Accuracy is dominated by the large `0-2` class; macro-F1 weights each
   bucket equally, so getting the middle buckets right counts.
2. Whether the probabilities are good: confident when right, not confident when wrong. That matters
   if you show probabilities to users or set thresholds later.
3. Train on data before T, test after T, repeat for several T. It mimics how the model will be
   used, catches results that depend on one lucky period, and shows drift.
4. A training label must be known at T. Labels can take 31 days to become final, so rows from the
   last 31 days before T can't be used.
5. A real, consistent gain over a simple rule, but the middle buckets remain weak. Good enough to
   show rough expectations; monitor it by month and test a stronger baseline before claiming more.
6. Rerun a fold from the code and compare: our 2025-07 fold reproduces every metric in
   `backtest.json` exactly (seeded sample, deterministic CatBoost). The JSON, figures and tables
   are written by the scripts, not by hand.
