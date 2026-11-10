"""Offline vs online features for the same day, on the real data (lesson 08).

For each day t given on the command line:
  * offline = the rows for requests submitted on t in data/dataset.parquet
    (training features, built once with labels as of 2025-12-31);
  * online  = build_daily_features() on only what the pipeline knew on t:
    requests submitted before t, closures before t (later ones are hidden),
    bounded by history_mask, with t's requests as the "first seen" rows.
It prints, per day, how many rows differ and in which numeric features.
After the label-finality fix every count should be 0.

Usage:  python analysis/train_serve_skew.py 2025-11-15 2025-03-03 [--data-dir DIR]
        (~1 minute per day plus ~20 s loading; needs data/dataset.parquet)
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sd311_core.cleaning import clean_requests  # noqa: E402
from sd311_core.features import CATEGORICAL_FEATURES, FEATURES  # noqa: E402
from sd311_core.io import DEFAULT_RAW_DIR, load_all  # noqa: E402
from sd311_core.serving import build_daily_features, history_mask  # noqa: E402

NUMERIC = [c for c in FEATURES if c not in CATEGORICAL_FEATURES]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("days", nargs="+")
    ap.add_argument("--data-dir", default=None)
    args = ap.parse_args(argv)

    raw, _ = load_all(args.data_dir or DEFAULT_RAW_DIR)
    cleaned = clean_requests(raw)
    offline_all = pd.read_parquet(ROOT / "data" / "dataset.parquet",
                                  columns=["service_request_id"] + NUMERIC)
    for day in args.days:
        t = pd.Timestamp(day)
        ids = set(cleaned.loc[cleaned["date_requested"].dt.normalize() == t, "service_request_id"])
        new = raw[raw["service_request_id"].isin(ids)].drop_duplicates("service_request_id").copy()
        new["date_closed"] = pd.NaT                       # first seen: not closed yet
        hist = raw[(raw["date_requested"] < t) & ~raw["service_request_id"].isin(ids)].copy()
        hist.loc[hist["date_closed"] >= t, "date_closed"] = pd.NaT   # closures not known yet
        hist = hist[history_mask(hist, t)]
        online = build_daily_features(hist, new, t).set_index("service_request_id").sort_index()
        offline = (offline_all[offline_all["service_request_id"].isin(online.index)]
                   .set_index("service_request_id").sort_index())
        on = online.loc[offline.index, NUMERIC].astype(float)
        off = offline[NUMERIC].astype(float)
        diff = ~((on == off) | (on.isna() & off.isna()))
        by = {c: int(v) for c, v in diff.sum().items() if v}
        print(f"{day}: {len(off):,} requests, {int(diff.any(axis=1).sum()):,} with a difference; "
              f"by feature {by}; mean mix90_31p online-offline "
              f"{float((on['mix90_31p'] - off['mix90_31p']).mean()):+.4f}")


if __name__ == "__main__":
    main()
