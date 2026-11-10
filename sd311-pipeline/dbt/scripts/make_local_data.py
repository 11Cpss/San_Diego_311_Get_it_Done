"""Build small local "bronze" Parquet files so dbt can run on DuckDB.

On Databricks, the bronze tables (sd311.bronze.open_snapshots and
sd311.bronze.closed_requests) are filled by databricks/tasks/load_bronze.py.
Locally we fake those two tables with Parquet files that have the SAME shape:
every source column as a string, plus snapshot_date, source and the ingest
metadata columns (_source_file, _ingested_at) that COPY INTO adds.

Input: the slim CSV extracts in /home/claude/data/raw (override with --raw).
The slim files lack a few contract columns (sap_notification_number,
street_address, iamfloc, floc, ...); we add them as nulls so the schema matches
the real bronze tables. public_description is special: the slim files carry only
its length (desc_len), so we write a placeholder text of that length ('xxx...').
That way length(public_description) in dbt/snapshots.py gives the real desc_len.

Usage:
    python dbt/scripts/make_local_data.py               # full 2024 + 2025 + open -> local_data/bronze
    python dbt/scripts/make_local_data.py --frac 0.01 --out dbt/fixtures/bronze
                                                        # the committed 1% CI fixture
"""
import argparse
import datetime as dt
from pathlib import Path

import pandas as pd

# The full bronze contract (all strings), in the order the Lambda writes them.
CONTRACT_COLUMNS = [
    "service_request_id", "service_request_parent_id", "sap_notification_number",
    "date_requested", "case_age_days", "case_record_type", "service_name",
    "service_name_detail", "date_closed", "status", "lat", "lng",
    "street_address", "zipcode", "council_district", "comm_plan_code",
    "comm_plan_name", "park_name", "case_origin", "referred", "iamfloc", "floc",
    "public_description", "specify_the_issue", "public_url",
]

# The open file was extracted on 2026-01-15 (latest date_requested is 2026-01-14),
# so we pretend both files arrived in that day's landing folder.
SNAPSHOT_DATE = "2026-01-15"


def to_bronze(csv_path: Path, source: str, frac: float, seed: int = 7) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str)  # bronze keeps everything as strings
    if frac < 1:
        df = df.sample(frac=frac, random_state=seed)
    if "public_description" not in df.columns and "desc_len" in df.columns:
        # The slim files keep only the description LENGTH (desc_len), not the text.
        # Real bronze has the text, and dbt/snapshots.py compute desc_len from it.
        # Stand-in: a placeholder text of the same length ('x' * desc_len), so
        # length(public_description) gives back the real desc_len. 0 -> NULL.
        n = pd.to_numeric(df["desc_len"], errors="coerce").fillna(0).astype(int)
        df["public_description"] = ["x" * k if k > 0 else None for k in n]
    for col in CONTRACT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    out = df[CONTRACT_COLUMNS].astype("string")
    out["snapshot_date"] = SNAPSHOT_DATE
    out["source"] = source
    # Ingest metadata, mimicking what COPY INTO adds in load_bronze.py
    out["_source_file"] = (
        f"/Volumes/sd311/bronze/landing/{source}/snapshot_date={SNAPSHOT_DATE}/{csv_path.stem}.parquet"
    )
    out["_ingested_at"] = pd.Timestamp(dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
    return out


def main() -> None:
    here = Path(__file__).resolve().parents[1]  # .../dbt
    p = argparse.ArgumentParser()
    p.add_argument("--raw", default="/home/claude/data/raw")
    p.add_argument("--out", default=str(here / "local_data" / "bronze"))
    p.add_argument("--years", default="2024,2025")
    p.add_argument("--frac", type=float, default=1.0)
    args = p.parse_args()

    raw, out = Path(args.raw), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    open_df = to_bronze(raw / "get_it_done_requests_open_datasd.slim.csv.gz", "open", args.frac)
    open_df.to_parquet(out / "open_snapshots.parquet", index=False)

    closed = [
        to_bronze(raw / f"get_it_done_requests_closed_{y}_datasd.slim.csv.gz", f"closed_{y}", args.frac)
        for y in args.years.split(",")
    ]
    closed_df = pd.concat(closed, ignore_index=True)
    closed_df.to_parquet(out / "closed_requests.parquet", index=False)

    print(f"open_snapshots:  {len(open_df):>8,} rows -> {out / 'open_snapshots.parquet'}")
    print(f"closed_requests: {len(closed_df):>8,} rows -> {out / 'closed_requests.parquet'}")


if __name__ == "__main__":
    main()
