"""Run the rolling-origin backtest (quarterly origins 2024-07 .. 2025-07).

Test months covered: 2024-07 through 2025-09.
Usage:  python scripts/run_backtest.py            (about 15-25 minutes on 2 CPUs)
Writes: reports/backtest.json, reports/backtest_predictions.parquet
"""
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sd311_core.backtest import make_fold, overall, run_fold  # noqa: E402

ORIGINS = ["2024-07-01", "2024-10-01", "2025-01-01", "2025-04-01", "2025-07-01"]


def main():
    data = pd.read_parquet(ROOT / "data" / "dataset.parquet")
    results, all_preds = [], []
    for origin in ORIGINS:
        t0 = time.time()
        res, preds, _ = run_fold(data, make_fold(origin))
        res["seconds"] = round(time.time() - t0)
        results.append(res)
        all_preds.append(preds)
        print(f"{origin}: model F1 {res['model']['macro_f1']:.3f} vs baseline "
              f"{res['baseline']['macro_f1']:.3f}  ({res['seconds']}s)", flush=True)
    preds = pd.concat(all_preds, ignore_index=True)
    out = {"folds": results, "overall": overall(preds)}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "backtest.json").write_text(json.dumps(out, indent=2))
    preds.to_parquet(ROOT / "reports" / "backtest_predictions.parquet", index=False)
    print(json.dumps({k: {m: v[m] for m in ("macro_f1", "accuracy", "log_loss")}
                      for k, v in out["overall"].items()}, indent=2))


if __name__ == "__main__":
    main()
