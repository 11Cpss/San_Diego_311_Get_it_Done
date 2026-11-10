"""Task 1 of sd311_daily: load newly landed Parquet files into the bronze tables.

The AWS ingest Lambda drops files here (one folder per source, one per day):

    /Volumes/sd311/bronze/landing/open/snapshot_date=2026-10-08/<file>.parquet
    /Volumes/sd311/bronze/landing/closed_2026/snapshot_date=2026-10-08/<file>.parquet
    /Volumes/sd311/bronze/landing/backfill/snapshot_date=2026-10-08/<file>.parquet

Every column is a string, plus `snapshot_date` and `source`.

This task:
  1. creates the catalog, the four schemas and the landing volume if missing;
  2. creates the two bronze Delta tables with an explicit all-string schema;
  3. runs COPY INTO for each table.

Why COPY INTO? It remembers which files it has already loaded (in the Delta
table's own metadata). Re-running the task after a failure, or twice in one day,
loads each file exactly once. That is the "idempotent" property: running it
again does not change the result.

The SQL is built by small functions so tests can check it without a cluster
(see databricks/tests/test_load_bronze.py).
"""
from __future__ import annotations

import argparse

# The bronze contract. All strings. `specify_the_issue` and `public_url` are only
# present in some files; COPY INTO leaves them NULL when a file lacks them.
SOURCE_COLUMNS = [
    "service_request_id", "service_request_parent_id", "sap_notification_number",
    "date_requested", "case_age_days", "case_record_type", "service_name",
    "service_name_detail", "date_closed", "status", "lat", "lng",
    "street_address", "zipcode", "council_district", "comm_plan_code",
    "comm_plan_name", "park_name", "case_origin", "referred", "iamfloc", "floc",
    "public_description", "specify_the_issue", "public_url",
    "snapshot_date", "source",
]

# Columns we add while loading, so every row can be traced back to its file.
METADATA_COLUMNS = {
    "_source_file": "STRING",      # full path of the Parquet file in the volume
    "_ingested_at": "TIMESTAMP",   # when COPY INTO loaded it (used by dbt source freshness)
}

SCHEMAS = ["bronze", "silver", "gold", "ml"]

# target table -> glob PATTERN (relative to the landing volume root)
TARGETS = {
    "open_snapshots": "open/snapshot_date=*/*.parquet",
    "closed_requests": "{closed_*,backfill}/snapshot_date=*/*.parquet",
}


def landing_path(catalog: str = "sd311") -> str:
    return f"/Volumes/{catalog}/bronze/landing"


def setup_statements(catalog: str = "sd311", create_catalog: bool = True) -> list[str]:
    """Idempotent DDL: catalog, schemas, volumes."""
    stmts = []
    if create_catalog:
        stmts.append(f"CREATE CATALOG IF NOT EXISTS {catalog}")
    stmts += [f"CREATE SCHEMA IF NOT EXISTS {catalog}.{s}" for s in SCHEMAS]
    stmts += [
        f"CREATE VOLUME IF NOT EXISTS {catalog}.bronze.landing",    # files from AWS
        f"CREATE VOLUME IF NOT EXISTS {catalog}.ml.run_summaries",  # run_summary.json for AWS
    ]
    return stmts


def create_table_sql(table: str, catalog: str = "sd311") -> str:
    """Bronze table with an explicit schema: every source column STRING + metadata."""
    cols = [f"{c} STRING" for c in SOURCE_COLUMNS]
    cols += [f"{c} {t}" for c, t in METADATA_COLUMNS.items()]
    body = ",\n    ".join(cols)
    return f"""CREATE TABLE IF NOT EXISTS {catalog}.bronze.{table} (
    {body}
) USING DELTA
COMMENT 'Raw Get It Done rows as landed by the AWS ingest Lambda (all strings).'"""


def copy_into_sql(table: str, catalog: str = "sd311") -> str:
    """COPY INTO for one bronze table.

    * FROM (SELECT ...) lets us add the metadata columns while loading.
    * PATTERN picks which sub-folders feed which table.
    * mergeSchema lets a file that lacks an optional column still load (it gets NULL).
    * The folder name snapshot_date=YYYY-MM-DD also looks like a Hive partition to
      Spark; it carries the same value as the in-file snapshot_date column, so the
      two agree. (Unverified on a real workspace - see STATUS_DATABRICKS.md.)
    """
    if table not in TARGETS:
        raise ValueError(f"unknown bronze table {table!r}; expected one of {sorted(TARGETS)}")
    return f"""COPY INTO {catalog}.bronze.{table}
FROM (
  SELECT *,
         _metadata.file_path AS _source_file,
         current_timestamp() AS _ingested_at
  FROM '{landing_path(catalog)}'
)
FILEFORMAT = PARQUET
PATTERN = '{TARGETS[table]}'
FORMAT_OPTIONS ('mergeSchema' = 'true')
COPY_OPTIONS ('mergeSchema' = 'true')"""


def all_statements(catalog: str = "sd311", create_catalog: bool = True) -> list[str]:
    """Everything this task runs, in order."""
    stmts = setup_statements(catalog, create_catalog)
    stmts += [create_table_sql(t, catalog) for t in TARGETS]
    stmts += [copy_into_sql(t, catalog) for t in TARGETS]
    return stmts


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs Databricks
    from pyspark.sql import SparkSession

    p = argparse.ArgumentParser()
    p.add_argument("--catalog", default="sd311")
    p.add_argument("--skip-create-catalog", action="store_true",
                   help="use when the catalog already exists or you lack CREATE CATALOG")
    args = p.parse_args(argv)

    spark = SparkSession.builder.getOrCreate()
    for stmt in all_statements(args.catalog, not args.skip_create_catalog):
        first_line = stmt.splitlines()[0]
        print(f"-- {first_line}")
        result = spark.sql(stmt)
        if stmt.startswith("COPY INTO"):
            # COPY INTO returns one row: num_affected_rows, num_inserted_rows, ...
            print(result.collect())


if __name__ == "__main__":
    main()
