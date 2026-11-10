"""Extra: how much does the model lean on desc_len / has_description?

The field-availability check found zero-length descriptions far more often among the newest open
requests than among closed ones (mostly in ESD Collections), so desc_len might be partly updated
after submission. This reruns ONE backtest fold (test 2025-07..09) without the two description
features and compares.

Usage:  python analysis/ablation_desc_len.py     (~5 minutes)
Writes: reports/ablation_desc_len.json
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import sd311_core.train as train_mod  # noqa: E402
from sd311_core.backtest import make_fold, run_fold  # noqa: E402


def main():
    data = pd.read_parquet(ROOT / "data" / "dataset.parquet")
    full = json.loads((ROOT / "reports" / "backtest.json").read_text())["folds"][-1]
    # Temporarily drop the two features from the list train.py uses.
    train_mod.FEATURES = [f for f in train_mod.FEATURES if f not in ("desc_len", "has_description")]
    res, _, _ = run_fold(data, make_fold("2025-07-01"), params={"verbose": 0})
    out = {"fold": res["fold"],
           "with_desc": {k: full["model"][k] for k in ("macro_f1", "accuracy", "log_loss")},
           "without_desc": {k: res["model"][k] for k in ("macro_f1", "accuracy", "log_loss")},
           "baseline": {k: res["baseline"][k] for k in ("macro_f1", "accuracy", "log_loss")}}
    (ROOT / "reports" / "ablation_desc_len.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
