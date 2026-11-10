"""Load -> clean -> label -> features, and cache the result as parquet.

Usage:  python scripts/build_dataset.py
Writes: data/dataset.parquet  (one row per cleaned request)
        data/meta.json        (extract date, as_of, row counts)
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sd311_core.cleaning import clean_requests  # noqa: E402
from sd311_core.features import build_features  # noqa: E402
from sd311_core.io import DEFAULT_RAW_DIR, load_all  # noqa: E402
from sd311_core.labels import DEFAULT_AS_OF, make_labels  # noqa: E402


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None,
                    help="folder with the slim CSVs (default: $SD311_DATA_DIR or ml/data/raw)")
    args = ap.parse_args(argv)
    t0 = time.time()
    raw, extract_date = load_all(args.data_dir or DEFAULT_RAW_DIR)
    print(f"raw rows {len(raw):,}  extract date {extract_date.date()}  ({time.time()-t0:.0f}s)")
    df = clean_requests(raw)
    del raw
    print(f"clean rows {len(df):,}")
    labels = make_labels(df, DEFAULT_AS_OF)
    feats = build_features(df, DEFAULT_AS_OF, labels=labels)
    print(f"features done ({time.time()-t0:.0f}s)")
    keep = ["service_request_id", "date_requested", "date_closed", "source", "status",
            "service_name_detail", "service_request_parent_id"]
    out = df[keep].join(labels).join(feats.drop(columns=["service_name"]).join(df[["service_name"]].astype("category")))
    (ROOT / "data").mkdir(exist_ok=True)
    out.to_parquet(ROOT / "data" / "dataset.parquet", index=False)
    meta = {"extract_date": str(extract_date.date()), "as_of": str(DEFAULT_AS_OF.date()),
            "n_clean": int(len(df)), "n_labeled": int(labels["bucket"].notna().sum())}
    (ROOT / "data" / "meta.json").write_text(json.dumps(meta, indent=2))
    print(meta, f"({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
