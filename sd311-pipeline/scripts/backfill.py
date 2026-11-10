#!/usr/bin/env python3
"""One-time backfill of historical yearly CSVs.

For every local file matching --pattern it:
  1. uploads the CSV as-is to  s3://<bucket>/backfill/<file>
  2. converts it to Parquet (all strings + snapshot_date + source) and PUTs it to
     <volume>/backfill/snapshot_date=<date>/<file>.parquet

`source` is derived from the filename, e.g. closed_2019. `.csv.gz` is fine:
pandas decompresses it, and the Parquet gets the plain name.

Examples
  python scripts/backfill.py --dir ~/data/raw --bucket my-bucket --dry-run
  DATABRICKS_HOST=... DATABRICKS_TOKEN=... \\
    python scripts/backfill.py --dir ~/data/raw --bucket my-bucket

Credentials: AWS from your normal profile/env; Databricks from env vars.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

NAME_RE = re.compile(r"get_it_done_requests_(?P<kind>open|closed_\d{4})_datasd")


def source_for(filename: str) -> str:
    m = NAME_RE.search(filename)
    return m.group("kind") if m else Path(filename).name.split(".")[0]


def plain_csv_name(filename: str) -> str:
    """get_x.slim.csv.gz -> get_x.slim.csv"""
    return filename[:-3] if filename.endswith(".gz") else filename


def plan(files: list[Path], volume_root: str, snapshot_date: str) -> list[dict]:
    from sd311.ingest import parquet_name

    steps = []
    for f in files:
        src = source_for(f.name)
        steps.append({
            "local": str(f),
            "source": src,
            "s3_key": f"backfill/{f.name}",
            # Flat backfill/ folder: the Databricks loader globs
            # backfill/snapshot_date=*/*.parquet; `source` is a column in the file.
            "volume_path": (f"{volume_root}/backfill/snapshot_date={snapshot_date}/"
                            f"{parquet_name(plain_csv_name(f.name))}"),
        })
    return steps


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, type=Path, help="folder with yearly CSVs")
    ap.add_argument("--pattern", default="get_it_done_requests_closed_*_datasd*.csv*")
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--volume-root", default="/Volumes/sd311/bronze/landing")
    ap.add_argument("--snapshot-date", default=date.today().isoformat())
    ap.add_argument("--skip-s3", action="store_true")
    ap.add_argument("--skip-volume", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, upload nothing")
    args = ap.parse_args(argv)

    files = sorted(args.dir.glob(args.pattern))
    if not files:
        print(f"no files match {args.pattern} in {args.dir}")
        return 1
    steps = plan(files, args.volume_root, args.snapshot_date)

    for s in steps:
        print(f"{'[dry-run] ' if args.dry_run else ''}{s['local']}\n"
              f"    -> s3://{args.bucket}/{s['s3_key']}\n    -> {s['volume_path']}")
    if args.dry_run:
        print(f"\n{len(steps)} file(s) would be uploaded.")
        return 0

    import boto3

    from sd311.config import Settings
    from sd311.ingest import DatabricksClient, http_session, read_csv_as_strings, to_parquet_bytes

    s3 = boto3.client("s3")
    dbx = None
    if not args.skip_volume:
        host, token = os.environ.get("DATABRICKS_HOST"), os.environ.get("DATABRICKS_TOKEN")
        if not (host and token):
            print("DATABRICKS_HOST/DATABRICKS_TOKEN not set (or use --skip-volume)")
            return 2
        dbx = DatabricksClient(host, token, http_session(Settings(bucket=args.bucket)),
                               timeout=300)

    for s in steps:
        path = Path(s["local"])
        if not args.skip_s3:
            s3.upload_file(str(path), args.bucket, s["s3_key"])  # multipart for big files
            print(f"uploaded s3://{args.bucket}/{s['s3_key']}")
        if dbx is not None:
            raw = path.read_bytes()
            df = read_csv_as_strings(gzip.decompress(raw) if path.suffix == ".gz" else raw)
            dbx.upload_file(s["volume_path"], to_parquet_bytes(df, args.snapshot_date,
                                                               s["source"]))
            print(f"uploaded {s['volume_path']} ({len(df):,} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
