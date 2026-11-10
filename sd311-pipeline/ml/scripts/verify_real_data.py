"""Does YOUR code reproduce the committed results on the real data?

    python scripts/verify_real_data.py labels             # lesson 2, ~1 min
    python scripts/verify_real_data.py fold 2025-07-01    # lesson 3, ~20 s (fold dates + baseline)
    python scripts/verify_real_data.py fold 2025-07-01 --with-model   # + CatBoost, ~7 min

``labels``: load + clean the raw CSVs, then run your ``make_labels`` and ``_backlog``
(via ``add_backlog_features``) on all ~2.56 M requests and compare with
``data/dataset.parquet``, which was built with the answer key. Every value must match.

``fold``: build the fold with your ``make_fold``, fit your ``fit_baseline`` on
``data/dataset.parquet`` and compare the fold dates, row counts and baseline metrics
with ``reports/backtest.json`` (the numbers in RESULTS.md). ``--with-model`` also
trains that fold's CatBoost model (300k-row sample, seed 0) and compares its metrics.

If the data is missing, the check prints SKIPPED and exits 0 (the unit tests are the
real checker; this one only shows that your code produces the reported numbers).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sd311_core.io import DEFAULT_RAW_DIR  # noqa: E402

DATASET = ROOT / "data" / "dataset.parquet"
BACKTEST = ROOT / "reports" / "backtest.json"


def _skip(msg: str) -> int:
    print(f"SKIPPED real-data check: {msg}")
    return 0


def _close(a: float, b: float, tol: float) -> bool:
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol)


def check_labels(raw_dir) -> int:
    from sd311_core.cleaning import clean_requests
    from sd311_core.features import add_backlog_features
    from sd311_core.io import load_all
    from sd311_core.labels import DEFAULT_AS_OF, make_labels

    raw_dir = Path(raw_dir or DEFAULT_RAW_DIR)
    if not DATASET.exists():
        return _skip(f"no {DATASET.relative_to(ROOT.parent)} (run scripts/build_dataset.py once)")
    if not list(raw_dir.glob("get_it_done_requests_*.slim.csv.gz")):
        return _skip(f"no slim CSVs in {raw_dir} (set SD311_DATA_DIR)")
    t0 = time.time()
    raw, _ = load_all(raw_dir)
    df = clean_requests(raw)
    del raw
    print(f"cleaned {len(df):,} requests ({time.time() - t0:.0f}s)")
    mine = df[["service_request_id"]].join(make_labels(df, DEFAULT_AS_OF)).join(add_backlog_features(df))
    print(f"your make_labels + backlog done ({time.time() - t0:.0f}s)")
    cols = ["days_to_close", "bucket", "label_final_at", "backlog_service", "backlog_district"]
    key = pd.read_parquet(DATASET, columns=["service_request_id"] + cols)
    if len(key) != len(mine) or not key["service_request_id"].equals(mine["service_request_id"].reset_index(drop=True)):
        print("FAIL: the cleaned rows do not line up with data/dataset.parquet (was it built from other data?)")
        return 1
    mine = mine.reset_index(drop=True)
    ok = True
    for c in cols:
        a, b = mine[c], key[c]
        if c == "bucket":
            a, b = a.astype("object"), b.astype("object")
        same = (a == b) | (a.isna() & b.isna())
        n_bad = int((~same).sum())
        ok &= n_bad == 0
        print(f"{'ok  ' if n_bad == 0 else 'DIFF'} {c:<16} {n_bad:,} of {len(a):,} rows differ")
        if n_bad:
            ex = mine.loc[~same].head(3).index
            print(pd.DataFrame({"service_request_id": key.loc[ex, "service_request_id"],
                                "yours": a.loc[ex], "answer key": b.loc[ex]}).to_string(index=False))
    n_lab = int(mine["bucket"].notna().sum())
    print(f"labeled rows: {n_lab:,} (data/meta.json says 2,541,007)")
    print("\nPASS: your labels and backlog reproduce data/dataset.parquet exactly" if ok
          else "\nFAIL: see the DIFF rows above")
    return 0 if ok else 1


def check_fold(origin: str, with_model: bool) -> int:
    from sd311_core.backtest import make_fold, run_fold
    from sd311_core.baseline import fit_baseline, predict_baseline
    from sd311_core.metrics import evaluate

    if not DATASET.exists():
        return _skip(f"no {DATASET.relative_to(ROOT.parent)} (run scripts/build_dataset.py once)")
    report = json.loads(BACKTEST.read_text())
    want = next((f for f in report["folds"] if f["fold"]["test_start"] == origin), None)
    if want is None:
        print(f"no fold with test_start {origin} in reports/backtest.json; "
              f"choose one of {[f['fold']['test_start'] for f in report['folds']]}")
        return 1
    fold = make_fold(origin)
    ok = fold.as_dict() == want["fold"]
    print(f"{'ok  ' if ok else 'DIFF'} fold dates   yours {fold.as_dict()}")
    if not ok:
        print(f"     report       {want['fold']}")

    t0 = time.time()
    data = pd.read_parquet(DATASET)
    t, lab = data["date_requested"], data["bucket"].notna()
    test = data[(t >= fold.test_start) & (t < fold.test_end) & lab]
    n_valid = int(((t >= fold.valid_start) & (t < fold.valid_end) & lab).sum())
    for name, got, exp in [("n_valid", n_valid, want["n_valid"]), ("n_test", len(test), want["n_test"])]:
        good = got == exp
        ok &= good
        print(f"{'ok  ' if good else 'DIFF'} {name:<12} yours {got:,}  report {exp:,}")

    base = fit_baseline(data, fold.test_start)
    pred, proba = predict_baseline(base, test["service_name"])
    m = evaluate(test["bucket"].astype(str).to_numpy(), pred, proba)
    print(f"baseline fitted and scored ({time.time() - t0:.0f}s)")
    for k in ("macro_f1", "accuracy", "log_loss"):
        good = _close(m[k], want["baseline"][k], 1e-9)
        ok &= good
        print(f"{'ok  ' if good else 'DIFF'} baseline {k:<9} yours {m[k]:.4f}  report {want['baseline'][k]:.4f}")

    if with_model:
        print("training the fold's CatBoost model (300k rows, seed 0; several minutes on 2 CPUs) ...", flush=True)
        res, _, _ = run_fold(data, fold)
        for k in ("macro_f1", "accuracy", "log_loss"):
            # CatBoost is deterministic for a fixed seed and thread count; allow tiny float noise.
            good = _close(res["model"][k], want["model"][k], 1e-3)
            ok &= good
            print(f"{'ok  ' if good else 'DIFF'} model    {k:<9} yours {res['model'][k]:.4f}  "
                  f"report {want['model'][k]:.4f}")
    print("\nPASS: your fold and baseline reproduce reports/backtest.json" if ok
          else "\nFAIL: see the DIFF rows above")
    return 0 if ok else 1


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="what", required=True)
    p = sub.add_parser("labels")
    p.add_argument("--data-dir", default=None)
    p = sub.add_parser("fold")
    p.add_argument("origin", nargs="?", default="2025-07-01")
    p.add_argument("--with-model", action="store_true")
    args = ap.parse_args(argv)
    if args.what == "labels":
        sys.exit(check_labels(args.data_dir))
    sys.exit(check_fold(args.origin, args.with_model))


if __name__ == "__main__":
    main()
