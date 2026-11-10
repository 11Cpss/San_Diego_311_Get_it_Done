# STATUS: integration of the model into sd311-pipeline (2026-10-08)

> Historical checkpoint from October 8, 2026. For the current AWS deployment,
> see [STATUS_AWS.md](STATUS_AWS.md). Databricks remains unconnected.

> **Update (Oct 8, evening):** the `exercise` setup described below was replaced by the lesson kit. Core functions are now stubs Jaden writes (`lessons/00_START_HERE.md`, `scripts/lessons.py`); `ml/exercises/` was removed. Test counts below predate that change: today, stubs in place = 44 passed / 159 skipped; all answer keys in place = 199 passed / 4 skipped.


Written 2026-10-08 at about 14:30 PDT, updated about 15:05 PDT after fixing two train/serve skew bugs and
retraining (section "Fixed 2026-10-08 (afternoon)"). Nothing was deployed, pushed or committed (no credentials,
and this folder is not a git repo). Every result below comes from a command that was actually
run on this machine. Anything not run is listed under "Not verified".

## What moved where

| from `sd311-model-v2/` | to `sd311-pipeline/` |
|---|---|
| `sd311_core/`, `scripts/`, `analysis/`, `exercises/`, `reports/`, `models/` | `ml/<same>` (copied, the original is untouched) |
| `tests/` | `ml/tests/` |
| `README.md`, `STATUS.md` | `ml/README.md` (paths updated), `ml/STATUS.md` (unchanged history) |
| `lessons/01–03` | `lessons/01–03` (a note at the top says code paths are relative to `ml/`) |
| `docs/adr/0002`, `0003` | `docs/adr/` (next to 0001) |
| `pytest.ini` | merged into the root `pyproject.toml` (`testpaths`, `exercise` marker deselected) |
| `data/` (90 MB) | not copied. Rebuild with `ml/scripts/build_dataset.py` (git-ignored as `ml/data/`) |

**Packaging (one install, two import roots).** In `pyproject.toml`,
`[tool.setuptools.packages.find] where = ["src", "ml"]` and `include = ["sd311", "sd311.*", "sd311_core", "sd311_core.*"]`
are spelled out. `pip install -e .` exposes `sd311` (src/) and `sd311_core` (ml/). It was checked from `/tmp`:
both import, and `pip wheel` builds one wheel that holds exactly `sd311/` and `sd311_core/`
(no tests or exercises). The model's extra dependencies are in `[project.optional-dependencies] ml`
(`catboost==1.2.10`, scikit-learn, mlflow, matplotlib). This is the simplest option. The cost is
one wheel name (`sd311`) for both packages, so the Databricks tasks also install the small AWS
package and its dependencies (boto3, pandera).

**Data path.** `sd311_core.io.DEFAULT_RAW_DIR` = `$SD311_DATA_DIR` or `<repo>/ml/data/raw`.
`ml/scripts/build_dataset.py` and `ml/analysis/field_availability.py` take `--data-dir`.
New: `ml/scripts/make_slim.py --src <city CSVs> [--data-dir DIR]` turns the full city CSVs into
the `.slim.csv.gz` files (tested: `ml/tests/test_make_slim.py`).

**.gitignore.** It ignores `ml/data/`, `ml/reports/backtest_predictions.parquet`, `dbt/local_data/`,
`*.duckdb`, `dist/`, `build/` and `mlruns/`. I checked with an external git dir
(`git --git-dir=<scratch> --work-tree=. check-ignore` / `add --dry-run`). Those paths are ignored.
`ml/models/model.cbm` (1.8 MB after the retrain), `metadata.json`, `ml/reports/backtest.json`, the figures and the
dbt fixtures (`dbt/fixtures/bronze/*.parquet`, 568 KB) are kept. 142 files would be committed (141 at the check, plus the new `ml/analysis/train_serve_skew.py`),
and the largest is model.cbm.

## What is implemented

**ML logic (plain pandas, unit-tested), new modules in `ml/sd311_core/`:**
- `serving.py`: `history_filter_sql` / `history_mask` define the bounded window (requests from the
  last 125 days, closures from the last 95 days, and everything still open).
  `prepare_requests` makes Spark-exported frames look like `io` output.
  `build_daily_features` builds features for today's first-seen requests, with history from the
  latest state, and uses first-seen values for the new rows. `score_frame` writes the predictions
  table rows.
- `monitoring.py`: `psi` (unseen categories pooled into `__other__`, 1e-4 floor), `drift_report`,
  `reference_distributions`, `live_metrics` (labels from `make_labels(as_of)`, final in the last
  28 days, first prediction per request), and `promotion_decision` (champion only if mean backtest
  log-loss < champion's AND macro-F1 > baseline in every fold).
- `registry.py`: `get_alias_version`, `champion_mean_log_loss`, `assign_alias` (sets the alias plus a
  `promotion_reason` version tag).
- `backtest.quarterly_origins(as_of, n)`: the last n quarter starts whose test window has final labels.
  For as_of 2025-12-31 it returns exactly the original 5 folds (2024-07 … 2025-07).

**Databricks tasks (thin Spark/MLflow glue):**
- `features.py`: reads `silver.requests_first_seen` (today) and `silver.requests_clean` (bounded
  window), calls `toPandas()` and `build_daily_features`, then writes `ml.features` (today's
  `snapshot_date` replaced).
- `score.py`: loads `models:/<catalog>.ml.closure_bucket@champion` (UC registry) and writes
  `ml.predictions` (id, snapshot_date, model_version, pred_bucket, p_0_2, p_3_7, p_8_30, p_31_plus).
  If there is no champion yet, it skips and the job continues.
- `train.py`: runs the backtest on the last 4 final quarters, logs params, per-fold metrics,
  `backtest_mean_log_loss`, `backtest.json` and `fold_macro_f1.png` to MLflow, fits the final model
  (same window as `fit_final.py`), logs it with a signature, registers it, applies
  champion/challenger, and logs `reference_distributions.json` and `promotion_decision.json`.
- `evaluate_and_summarize.py`: writes the full v1 `run_summary.json` with `data_quality_failures`
  (4 post-scoring checks), `freshness_hours`, `model.version/metrics` (live macro-F1, log-loss) and
  `drift.max_psi` / `psi_by_feature` (last 7 days vs the champion's reference).
  **Timezone fix:** city timestamps are localised to America/Los_Angeles and converted to UTC before
  the subtraction. The date folder is the Pacific date of the run (`pacific_date(now)`).
- `databricks.yml`: an `artifacts:` block builds the repo wheel (`pip wheel` at the repo root,
  output in `dist/`). The `ml` environment (daily + train jobs) installs `../dist/*.whl`,
  `catboost==1.2.10`, pandas, scikit-learn, `mlflow>=3.1` and matplotlib. `evaluate_and_summarize`
  now uses the `ml` environment. The old `sd311_core_wheel` variable was removed.

**Small fixes:**
- `deploy.yml`: `BUNDLE_VAR_warehouse_id` / `BUNDLE_VAR_notification_email` come from repo/env vars
  `DATABRICKS_WAREHOUSE_ID` / `DATABRICKS_NOTIFICATION_EMAIL` (documented in the README and the
  workflow header).
- `infra/main.tf`: ingest Lambda `memory_size = 2048`.
- `ci.yml`: installs `.[dev,ml]` + `databricks/requirements-dev.txt` and Java 17 (for pyspark),
  then runs one `pytest` over all three suites with `MLFLOW_ALLOW_FILE_STORE=true`.

**Docs:** `lessons/08_mlflow_scoring_monitoring.md` (new), top-level `README.md` (folder map with
`ml/`, table of lessons 01–09, install/test commands, deploy vars, Lambda memory),
`ml/README.md`, `databricks/README.md` (task table, wheel).

## Test results (exact, final run 2026-10-08 ~15:00 PDT, after the skew fixes and the retrain)

| command | result |
|---|---|
| `python3 -m pytest` (repo root: tests/, ml/tests, databricks/tests) | **102 passed, 4 skipped, 9 deselected** (was 95 before the skew fixes) |
| `python3 -m pytest tests` | 32 passed |
| `python3 -m pytest ml/tests` | 43 passed, 9 deselected (18 original incl. 1 new label test + 10 serving (5 new) + 13 monitoring + 1 registry + 1 make_slim) |
| `python3 -m pytest databricks/tests` | 27 passed, 4 skipped (no pyspark / databricks-bundles in system Python; 1 new desc_len test) |
| `/home/claude/.venv-spark/bin/python -m pytest databricks/tests` | 30 passed, 1 skipped (the monitor round-trip test skips because there is no boto3 in that venv) |
| `python3 -m pytest -m exercise ml/tests` | 9 failed, 43 deselected (expected: Jaden's exercise is not implemented yet) |
| `cd dbt && dbt build --target duckdb --profiles-dir .` | PASS=47 WARN=0 ERROR=0 (new: `not_null` on `requests_clean.desc_len`) |
| `ruff check .` | All checks passed (`ml/`, `databricks/`, `dbt/` excluded as before) |

Mutation check: when the window was shortened to 40/10 days,
`test_history_window_gives_same_features_as_full_history` failed (then I restored it).

**Real-data checks (scratch scripts, not part of the test suite):**
- On the full raw data, snapshot 2025-11-15 (1,178 new requests, window 319,307 of 2.9M rows,
  about 20 s), the daily path gives **identical** calendar, workload and backlog features to the
  offline `dataset.parquet`. `model.cbm` scored them through `score_frame`.
  (1,178 is that earlier check's count; the cleaned data has 551 requests on that day.)
- After the skew fixes and the retrain (re-run 14:59 PDT,
  history = only what was known on that day): the 551 cleaned requests of 2025-11-15 and the 1,313
  of 2025-03-03 have **all** numeric features equal to `dataset.parquet`
  (`ml/analysis/train_serve_skew.py`), and the new `model.cbm` scored the 551 through `score_frame`
  (190 × 0-2, 147 × 3-7, 36 × 8-30, 178 × 31+).
- The pandas part of `train.py` on the real data (2,557,712 cleaned rows, same as `ml/STATUS.md`),
  1 fold, 20k-row sample: model F1 0.494 vs baseline 0.371, rule says champion. Reference
  distribution: 35 services, 13 origins.
- The exact MLflow calls in `train.py`/`score.py` (`catboost.log_model(name=…, signature=…)`,
  `register_model`, aliases, `load_model("models:/…@champion")`, `artifacts.load_dict`) ran
  against a local sqlite registry with MLflow 3.17.0.

## Fixed 2026-10-08 (afternoon): two train/serve skew bugs

Bug 1 was found by building the features for one real day twice (offline `dataset.parquet` vs the
daily path) and comparing them. Bug 2 was found by checking where the daily path gets each
feature's input. Lesson 08 has a section on them ("A real bug we found").

**1. Bucket-mix finality (`mix90_*`).** `make_labels` treated a slow request that *later* closed
as final only at `date_closed + 1`, but "31+" was certain at `date_requested + 31 days`. The daily
path (`as_of = today`) already counted such requests at day 31, while the offline dataset
(`as_of = 2025-12-31`) counted them at the close date. Re-measured today with
`ml/analysis/train_serve_skew.py` logic and the old `labels.py`: on 2025-11-15, 546 of 551 new
requests differed (mean `mix90_31p` +0.029 at serving); on 2025-03-03, 1,313 of 1,313 (+0.022).
(The earlier note said "1,160 of 1,178" for 2025-11-15; I could not reproduce that row count.
2025-11-15 has 551 cleaned requests on this data, so that figure probably came from a different
selection.)
- Fix (`ml/sd311_core/labels.py`): `label_final_at = min(date_closed + 1, date_requested + 31d)`
  for "31+" labels (always `date_requested + 31d`), `date_closed + 1` (end of the close day) for the
  others. Training (`features.py`) and serving (`serving.py`) both get their labels from
  `make_labels`, so one change covers both. The baseline (`baseline.py`) and live metrics
  (`monitoring.py`) also use `label_final_at` and follow the new rule. dbt `ml_labels` stores no
  finality timestamp (only bucket + `is_censored_31_plus`), so only a comment was added there.
- After the fix: 0 differing rows on both real days (all numeric features equal).
- Tests: `ml/tests/test_serving.py::test_offline_training_features_equal_daily_serving_features`
  (3 days; offline features with `as_of = 2025-12-31` vs daily features from "what was known on
  day t"). Mutation check: with the old rule it fails on all 3 days.
  `ml/tests/test_labels.py::test_late_closed_31_plus_is_final_at_day_31_not_at_close`.

**2. `desc_len` was 0 at serving.** Silver had no description, so `prepare_requests` set
`desc_len = 0` for every scored request (training: real length; `has_description = 0` for 13% of
all rows).
- Fix: `desc_len = COALESCE(LENGTH(public_description), 0)` is computed where data enters silver:
  `databricks/tasks/snapshots.py` (new INT column in `requests_first_seen` and `requests_current`,
  set from the bronze row) and dbt `stg_open_snapshots` / `stg_closed_requests` → `requests_clean`
  (new `desc_len` column, `not_null` test). The daily features read the first-seen `desc_len`.
  `prepare_requests` computes it from `public_description` if only the text is there, and warns if
  neither column exists.
- Local data: the slim CSVs have `desc_len` but no text. `dbt/scripts/make_local_data.py` now
  writes a placeholder `public_description` of that length ('x' * desc_len, NULL for 0), so
  `length()` returns the real value. The committed fixtures (`dbt/fixtures/bronze/*.parquet`) were
  patched the same way: I regenerated them with the new script (same `--frac 0.01`, same seed)
  and checked that every other column is identical, then copied in only `public_description`
  (568 KB total now, was 540 KB). `dbt/local_data/` (git-ignored, full 2024–2025) was **not** regenerated:
  run `python dbt/scripts/make_local_data.py` to get real `desc_len` there (without it, every
  value is 0, which is not an error).
- Tests: `databricks/tests/test_snapshots.py::test_desc_len_is_derived_from_public_description`,
  `ml/tests/test_serving.py::test_desc_len_from_first_seen_reaches_features`,
  `test_prepare_requests_computes_desc_len_from_text`.
- **Deploy note:** the silver tables are created with `CREATE TABLE IF NOT EXISTS`. Nothing has
  been deployed, so they will get the new column. If they already existed somewhere, run
  `ALTER TABLE sd311.silver.requests_first_seen ADD COLUMNS (desc_len INT)` (same for
  `requests_current`) first. Rows inserted before that would have NULL `desc_len`.

**Retrain (all three steps finished on this machine, 14:30–14:57 PDT).**
`build_dataset.py` (33 s, 2,557,712 cleaned rows, 2,541,007 labeled: same as before, only
`label_final_at` moved) → `run_backtest.py` (23 min) → `make_figures.py` → `fit_final.py` (~4 min).
`ml/models/model.cbm`, `metadata.json`, `ml/reports/backtest.json`, `backtest_predictions.parquet`,
`results_tables.md` and the 4 figures were all rewritten by these runs. RESULTS.md, lesson 03 and
the resume bullet use the new numbers.

| pooled backtest (456,265 test requests) | before | after |
|---|---|---|
| model macro-F1 / accuracy / log-loss | 0.532 / 0.603 / 0.915 | **0.531 / 0.601 / 0.917** |
| baseline macro-F1 / accuracy / log-loss | 0.424 / 0.525 / 1.070 | **0.432 / 0.535 / 1.065** |
| model recall 0-2 / 3-7 / 8-30 / 31+ | 0.77 / 0.36 / 0.36 / 0.64 | 0.76 / 0.35 / 0.37 / 0.65 |
| final model: trees / validation log-loss (2025-11) | 307 / 0.931 | 253 / 0.954 |

Per-fold model macro-F1 is now 0.550, 0.508, 0.529, 0.528, 0.519 (was 0.539, 0.532, 0.518, 0.536,
0.529). The model still beats the baseline in every fold on macro-F1, accuracy and log-loss. The
baseline improved slightly (its 90-day mix uses the same finality rule), so the gap shrank from
+0.108 to +0.099 macro-F1. The model's pooled numbers moved by about 0.002, which is noise level for one
seed and a 300k sample. The 2024-10 fold (0.532 → 0.508, early stopping at 59 trees) and the
final model's validation log-loss (+0.023) moved more. I did not investigate them further (no time
for multiple seeds). The `desc_len` ablation (RESULTS.md) was **not** re-run and is marked as pre-fix.


## Not verified (needs a real workspace / AWS account)

- None of the four ML tasks has run on Databricks. Untested: Spark reads, `toPandas()` memory on
  serverless, Delta `replaceWhere` writes (including the first write that creates the table), the
  UC registry (`databricks-uc`), `mlflow.set_experiment("/Shared/…")`, and `{{job.run_id}}`.
- `databricks bundle validate`/`deploy` were not run (no CLI or credentials). In particular: an
  `artifacts.path` (`..`) outside the bundle root, and `../dist/*.whl` inside a **serverless
  environment** `dependencies` list. The docs I fetched show `whl: ./dist/*.whl` for job-task
  libraries and `dist/*.whl` in a serverless *pipeline* environment, but no serverless-job example.
  Fallback: upload the wheel to a volume and list its `/Volumes/...whl` path (the previous setup).
- `train.py` on the full history in a serverless driver (~2.5M rows into pandas). Use `--since`
  if memory is short.
- DQ checks run after scoring. dbt test failures stop the job earlier (then the monitor sees
  `JobMissing`).
- Terraform (2048 MB) and the workflow edits are not `terraform validate`d or run in GitHub
  Actions. The YAML parses.

## Commands to run everything locally

```bash
cd /home/claude/sd311-pipeline
pip install --break-system-packages -e ".[dev,ml]"          # sd311 + sd311_core + catboost/mlflow
pip install --break-system-packages -r databricks/requirements-dev.txt   # pyspark may need a venv (see STATUS_DATABRICKS.md)

ruff check .
python3 -m pytest                          # 102 passed, 4 skipped, 9 deselected
python3 -m pytest -m exercise ml/tests     # exercise checker (fails until implemented)
/home/claude/.venv-spark/bin/python -m pytest databricks/tests   # Spark parser + bundle schema checks
cd dbt && dbt build --target duckdb --profiles-dir . && cd ..       # PASS=47

# model pipeline (data in ml/data/raw or SD311_DATA_DIR)
python3 ml/scripts/make_slim.py --src <folder with city CSVs>      # only if you have the full CSVs
export SD311_DATA_DIR=/home/claude/data/raw                          # existing slim files on this box
python3 ml/scripts/build_dataset.py        # ~40 s -> ml/data/dataset.parquet
python3 ml/scripts/run_backtest.py         # ~23 min on 2 CPUs (14:30-14:53 today)
python3 ml/scripts/make_figures.py
python3 ml/scripts/fit_final.py            # ~7 min -> ml/models/model.cbm
```

