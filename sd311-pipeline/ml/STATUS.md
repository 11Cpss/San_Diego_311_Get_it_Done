# STATUS: sd311-model-v2 (2026-10-08)

> **Superseded numbers.** This file is the model's history from before the integration. On
> 2026-10-08 (afternoon) the label-finality fix (`label_final_at` for "31+" = request + 31 days)
> changed the training features, and the backtest and final model were re-run. The current
> numbers are in `reports/RESULTS.md` (pooled macro-F1 0.531 vs baseline 0.432, log-loss 0.917
> vs 1.065; final model 253 trees, validation log-loss 0.954) and `../STATUS_INTEGRATION.md`.

## Done
- `sd311_core/` package: io, cleaning, labels, features, baseline, metrics, train, backtest.
- Dataset build: 2,962,094 raw rows become 2,557,712 cleaned requests (closed + open), of which
  2,541,007 are labeled as of 2025-12-31. The open-file extract date is inferred as
  **2026-01-14**: `date(date_requested) + case_age_days` gives that day for about 98% of open rows.
- Tests: `pytest` gives 17 passed (label edge cases plus leakage/invariance tests). The exercise
  checker has 9 tests, excluded by default and verified against a naive reference solution.
- Rolling-origin backtest (5 quarterly folds, test months 2024-07..2025-09) vs baseline:
  `reports/backtest.json`, `reports/backtest_predictions.parquet`.
- Final model: `models/model.cbm` and `models/metadata.json`.
- Analyses: `analysis/field_availability.py`, `analysis/old_vs_new.py`,
  `analysis/ablation_desc_len.py` (extra).
- `reports/RESULTS.md` with 4 figures, lessons 01-03, ADR 0002/0003, README.

## Not done / cut
- **No Optuna search.** Fixed CatBoost settings with early stopping on the validation month. The
  spec allowed "at most light", and compute went to the backtest instead.
- Training uses a random **300k-row sample** per window (2-CPU budget), not the full ~800k.
- Only one baseline (per-service 90-day mix). A service x neighborhood or service x origin baseline
  is suggested as lesson 3's exercise.
- No calibration analysis beyond log-loss.

## Key numbers (all from running the code)
| | model | baseline |
|---|---|---|
| macro-F1 (pooled, 456,265 test requests) | **0.532** | 0.424 |
| accuracy | 0.603 | 0.525 |
| log-loss | 0.915 | 1.070 |
| recall 0-2 / 3-7 / 8-30 / 31+ | 0.77 / 0.36 / 0.36 / 0.64 | 0.76 / 0.17 / 0.29 / 0.49 |

Per fold, model macro-F1 vs baseline is 0.539/0.495, 0.532/0.386, 0.518/0.336, 0.536/0.380 and
0.529/0.371. The model wins every fold, with the smallest margin in the first.

v1 reproduction: global-median MAE is 9.71 and per-service-median MAE is 25.93 on v1's truncated test
set. 22,940 of 180,360 H2-2025 requests (12.7%) were missing from that set. The Q3-2025 `31+`
share is 21.4% with proper labels vs 14.4% in v1's set.

Ablation (extra, 2025-07 fold only): without `desc_len`/`has_description`, macro-F1 is 0.528 vs 0.529,
accuracy 0.593 vs 0.607 and log-loss 0.912 vs 0.897. The gain over the baseline does not depend on the
description features.

Final model: trained 2024-01-01..2025-10-31 (300k sample), validated on 2025-11, 307 trees,
validation log-loss 0.931. Top features: case_origin, service_name, case_record_type, mix90_8_30, service_x_comm.

## Caveats
- **January gap:** requests closed between 2026-01-01 and 2026-01-14 are in neither file. Labels use
  as_of 2025-12-31, and evaluation stops at submissions on or before 2025-09-30. Those cases are still missing
  (estimated at about 0.2% of requests, mostly `31+`).
- **Snapshot data:** fields may have been edited after submission. Daily snapshots from the pipeline
  are needed for true as-of features.
- **service_name_detail excluded as a precaution.** After matching the service mix, the
  missingness evidence for leakage disappears (68.8% vs 68.9%). See ADR 0003 for the honest version.
- **desc_len is slightly suspicious.** Zero-length descriptions are much more common among the newest
  open requests (mostly ESD Collections), but the model barely uses it (see the ablation).
- **Middle buckets are weak.** Recall for 3-7 and 8-30 is about 0.36.
- **Model-version note:** the final model has no held-out test period of its own, so its expected
  quality is the backtest's.

## What the pipeline should import
```python
from sd311_core.io import load_all, load_closed, load_open, infer_extract_date
#   load_all(raw_dir=DEFAULT_RAW_DIR) -> (df: DataFrame, extract_date: Timestamp)
#   infer_extract_date(open_df: DataFrame) -> Timestamp
from sd311_core.cleaning import clean_requests
#   clean_requests(df: DataFrame) -> DataFrame   (input needs column `source` in {"closed","open"})
from sd311_core.labels import make_labels, BUCKETS
#   make_labels(df, as_of: Timestamp) -> DataFrame[days_to_close, bucket, label_final_at]
from sd311_core.features import build_features, FEATURES, CATEGORICAL_FEATURES
#   build_features(df, as_of, labels=None) -> DataFrame  (one row per row of df)
from sd311_core.train import load_model, predict, fit_model
#   load_model(path) -> CatBoostClassifier
#   predict(model, feature_df) -> (pred_labels: ndarray[str], proba: ndarray[n, 4] in BUCKETS order)
#   fit_model(train_df, valid_df=None, params=None) -> CatBoostClassifier
from sd311_core.baseline import fit_baseline, predict_baseline
from sd311_core.metrics import evaluate
#   evaluate(y_true, y_pred, proba) -> dict(macro_f1, accuracy, log_loss, recall, confusion)
from sd311_core.backtest import make_fold, run_fold
```
Daily scoring recipe: load the **full** history (closed + open, because the window features need
past requests), run `clean_requests`, then `build_features(df, as_of=<today>)`, select the new rows,
and run `predict(load_model("models/model.cbm"), rows)`. Use `as_of = today` (not 2025-12-31) so the
bucket-mix feature only uses labels final by today. The input must have the columns listed in
`sd311_core/io.py` (same names as the slim CSVs).

## Suggested next steps
1. Have the pipeline store a daily snapshot of new requests. After 2-4 weeks, check whether
   `service_name_detail`, `desc_len` or `service_name` change after submission (ADR 0003 follow-up).
2. Add a stronger baseline (service x origin, service x neighborhood) and re-run the backtest.
3. Light Optuna search on the validation month only (depth, lr, l2), then train on full windows.
4. Monitor monthly macro-F1 and the per-class recall. Retrain monthly with the same 31-day embargo.
5. Consider a second model for `31+` cases: how long beyond a month (survival analysis handles open cases natively).
