"""Fit the final model on the most recent window and save it.

Window (as_of = 2025-12-31, so "today" for labels is 2026-01-01):
    train  2024-01-01 .. 2025-10-31   (sampled to 300k rows, same as backtest)
    valid  2025-11-01 .. 2025-11-30   (early stopping only)
    embargo: requests from the last 31 days before 2026-01-01 have no final label yet.

Usage:  python scripts/fit_final.py
Writes: models/model.cbm, models/metadata.json
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sd311_core.backtest import make_fold  # noqa: E402
from sd311_core.features import CATEGORICAL_FEATURES, EXCLUDED_FEATURES, FEATURES  # noqa: E402
from sd311_core.labels import BUCKETS, DEFAULT_AS_OF  # noqa: E402
from sd311_core.train import DEFAULT_PARAMS, fit_model  # noqa: E402

MAX_TRAIN_ROWS = 300_000


def main():
    data = pd.read_parquet(ROOT / "data" / "dataset.parquet")
    meta_data = json.loads((ROOT / "data" / "meta.json").read_text())
    fold = make_fold(DEFAULT_AS_OF + pd.Timedelta(days=1))  # pretend the next "test" starts 2026-01-01
    t = data["date_requested"]
    labeled = data["bucket"].notna()
    train = data[(t >= fold.train_start) & (t < fold.valid_start) & labeled]
    valid = data[(t >= fold.valid_start) & (t < fold.valid_end) & labeled]
    if len(train) > MAX_TRAIN_ROWS:
        train = train.sample(MAX_TRAIN_ROWS, random_state=0)
    model = fit_model(train, valid)
    (ROOT / "models").mkdir(exist_ok=True)
    model.save_model(str(ROOT / "models" / "model.cbm"))

    backtest = json.loads((ROOT / "reports" / "backtest.json").read_text())
    metadata = {
        "model": "CatBoostClassifier MultiClass",
        "classes": BUCKETS,
        "features": FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "excluded_features": EXCLUDED_FEATURES,
        "train_window": {"start": str(fold.train_start.date()), "end_exclusive": str(fold.valid_start.date())},
        "valid_window": {"start": str(fold.valid_start.date()), "end_exclusive": str(fold.valid_end.date())},
        "n_train": int(len(train)), "n_valid": int(len(valid)),
        "best_iteration": int(model.get_best_iteration() or 0),
        "params": {k: v for k, v in DEFAULT_PARAMS.items() if k != "class_names"},
        "as_of": str(DEFAULT_AS_OF.date()),
        "open_file_extract_date": meta_data["extract_date"],
        "backtest_overall": {who: {m: backtest["overall"][who][m]
                                   for m in ("macro_f1", "accuracy", "log_loss", "recall")}
                             for who in ("model", "baseline")},
        "data_notes": [
            "Labels as-of 2025-12-31: closed by then -> exact bucket; open/closed later and >=31 days old "
            "-> 31+; else unlabeled. A 31+ label is final (label_final_at) at date_requested + 31 days, "
            "also when the request closed later; other labels at the end of the close day.",
            "Open snapshot extract date inferred as date(date_requested)+case_age_days (mode).",
            "Requests closed 2026-01-01..extract appear in neither file (no 2026 closed file); "
            "evaluation stops at requests submitted 2025-09-30.",
            "Training rows are a random 300k sample of the window (compute budget: 2 CPUs).",
            "Features must be built with sd311_core.features.build_features on the full request history "
            "(workload/backlog/mix need past requests, open and closed).",
        ],
    }
    (ROOT / "models" / "metadata.json").write_text(json.dumps(metadata, indent=2))
    print(json.dumps({k: metadata[k] for k in ("train_window", "valid_window", "n_train", "best_iteration")}, indent=2))


if __name__ == "__main__":
    main()
