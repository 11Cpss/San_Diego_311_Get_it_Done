# sd311-model-v2: San Diego "Get It Done" closure-time model

Given a new 311 request, this model predicts which bucket its closure time will fall in:
**0-2, 3-7, 8-30 or 31+ days**. It uses only information available when the request is
submitted.

This is a rebuild of the original notebook project. It fixes these problems: a truncated test set,
probable leakage, workload features that undercounted, and model selection on the test set. See
`lessons/01_why_the_old_numbers_were_wrong.md`.

## Setup

This folder is part of the `sd311-pipeline` repo. `sd311_core` is installed together with
the AWS package from the repo root:

```bash
cd sd311-pipeline                 # repo root
pip install -e ".[dev,ml]"        # exposes sd311 (src/) and sd311_core (ml/)
cd ml
```

Raw data: the slim CSVs (`get_it_done_requests_closed_YYYY_datasd.slim.csv.gz`,
`get_it_done_requests_open_datasd.slim.csv.gz`) are read from, in order of priority,
the scripts' `--data-dir` flag, the env var `SD311_DATA_DIR`, or `ml/data/raw/` (git-ignored).
Make them from the city's full CSVs with:

```bash
python scripts/make_slim.py --src <folder with the city CSVs> [--data-dir data/raw]
```

## Run

```bash
pytest tests                             # model tests (from ml/; or plain `pytest` at the repo root for everything)
python ../scripts/lessons.py check 2     # lesson checker (from the repo root: python scripts/lessons.py check 2)

python scripts/build_dataset.py [--data-dir DIR]  # load, clean, label, features -> data/dataset.parquet (~40 s)
python analysis/field_availability.py    # is a field filled in after submission? -> reports/
python analysis/old_vs_new.py            # v1 vs v2 evaluation population     -> reports/
python analysis/old_vs_new.py --verify   # recompute and compare with reports/old_vs_new.json
python scripts/verify_real_data.py labels          # your labels + backlog vs data/dataset.parquet (~1 min)
python scripts/verify_real_data.py fold 2025-07-01 # your fold + baseline vs reports/backtest.json (~20 s)
python analysis/train_serve_skew.py 2025-11-15   # offline vs daily features for one day (expect 0 diffs)
nohup python scripts/run_backtest.py > reports/backtest.log 2>&1 &   # 5 folds, ~25 min on 2 CPUs
python scripts/make_figures.py           # figures + tables from reports/backtest.json
python scripts/fit_final.py              # models/model.cbm + models/metadata.json (~7 min)
```

## Folder map

```
sd311_core/            the package (the daily pipeline imports from here)
  io.py                load raw files; infer open-file extract date
  cleaning.py          cleaning rules (closed + open together, prefer closed on duplicates)
  labels.py            4 buckets with an as-of date; label_final_at (31+ final at day 31)
  features.py          submission-time features (searchsorted window counts)
  baseline.py          per-service trailing-90-day bucket-mix baseline
  metrics.py           macro-F1, accuracy, log-loss, recall, confusion matrix
  train.py             CatBoost MultiClass fit / predict
  backtest.py          rolling-origin folds with a 31-day embargo; quarterly_origins
  serving.py           daily features on a bounded history window + scoring (Databricks)
  monitoring.py        PSI drift, live metrics, champion/challenger promotion rule
  registry.py          MLflow alias helpers (champion / challenger)
scripts/               make_slim, build_dataset, run_backtest, make_figures, fit_final, verify_real_data
analysis/              field_availability.py, old_vs_new.py, train_serve_skew.py
tests/                 label edge cases, leakage tests, backtest/baseline, serving, monitoring, registry
                       (tests marked lesson1/2/3/8 check the functions Jaden writes in those lessons;
                        they are skipped until the stub is replaced, see ../lessons/00_START_HERE.md)
reports/               RESULTS.md, figures/, backtest.json, analysis outputs
../lessons/            01-03 (model) teaching notes, plus 04-09 for the pipeline
../docs/adr/           0002 buckets, 0003 exclude service_name_detail
models/                model.cbm, metadata.json
STATUS.md              what is done, key numbers, caveats, pipeline interface
```
