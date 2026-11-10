"""Convert the city's full Get It Done CSVs into the slim files sd311_core reads.

The city publishes (https://data.sandiego.gov/datasets/get-it-done-311/):
    get_it_done_requests_closed_YYYY_datasd.csv   one file per closure year
    get_it_done_requests_open_datasd.csv          requests open at export time

sd311_core.io expects, in the same folder:
    get_it_done_requests_closed_YYYY_datasd.slim.csv.gz
    get_it_done_requests_open_datasd.slim.csv.gz

"Slim" = only the columns the model uses, plus ``desc_len`` (the length of
public_description) instead of the free text itself. That shrinks the files a
lot and keeps description text (which may contain personal details) out of the
model data.

Usage:
    python ml/scripts/make_slim.py --src ~/Downloads/get_it_done --data-dir ml/data/raw
    python ml/scripts/make_slim.py --src ~/Downloads/get_it_done   # writes to $SD311_DATA_DIR or ml/data/raw
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

KEEP = [
    "service_request_id", "service_request_parent_id", "date_requested", "case_age_days",
    "case_record_type", "service_name", "service_name_detail", "date_closed", "status",
    "lat", "lng", "zipcode", "council_district", "comm_plan_code", "comm_plan_name",
    "case_origin", "referred", "park_name",
]
PATTERN = re.compile(r"^get_it_done_requests_(closed_\d{4}|open)_datasd\.csv$")


def slim_frame(df: pd.DataFrame) -> pd.DataFrame:
    """All-string frame in, slim frame out (KEEP columns + desc_len)."""
    out = df.reindex(columns=KEEP)  # a missing column becomes all-NaN instead of crashing
    if "public_description" in df.columns:
        out["desc_len"] = df["public_description"].fillna("").str.len().astype("int64")
    else:
        out["desc_len"] = 0
    return out


def slim_file(src: Path, dst_dir: Path) -> Path:
    """Read one full CSV as strings and write ``<name>.slim.csv.gz`` into dst_dir."""
    df = pd.read_csv(src, dtype=str, keep_default_na=True)
    dst = dst_dir / src.name.replace(".csv", ".slim.csv.gz")
    slim_frame(df).to_csv(dst, index=False, compression="gzip")
    return dst


def main(argv=None):
    from sd311_core.io import DEFAULT_RAW_DIR

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True, help="folder with the city's full CSVs")
    ap.add_argument("--data-dir", default=None,
                    help="output folder (default: $SD311_DATA_DIR or ml/data/raw)")
    args = ap.parse_args(argv)
    src = Path(args.src)
    dst = Path(args.data_dir) if args.data_dir else DEFAULT_RAW_DIR
    dst.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src.iterdir() if PATTERN.match(p.name))
    if not files:
        raise SystemExit(f"no get_it_done_requests_*_datasd.csv files in {src}")
    for f in files:
        out = slim_file(f, dst)
        print(f"{f.name} -> {out}")


if __name__ == "__main__":
    main()
