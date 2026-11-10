"""Task 4 of sd311_daily: build model features for requests first seen today.

Reads (Unity Catalog, catalog from --catalog):
    silver.requests_first_seen   today's new requests, fields AS FIRST SEEN
    silver.requests_clean        (dbt) cleaned latest state = history for the window counts
Writes:
    ml.features                  one row per (service_request_id, snapshot_date);
                                 today's partition is replaced, earlier days are kept

Design choice: Spark only filters; pandas builds the features.
    The window features (30-day workload, 90-day bucket mix, open backlog) are
    already written and tested in pandas in sd311_core.features. For one scoring
    day we only need a bounded slice of history (requests from the last ~125 days,
    closures from the last ~95 days, and everything still open), which is a few
    hundred thousand rows - fine for toPandas() on serverless. Re-writing the
    features in Spark would mean two implementations to keep in sync. If the data
    grows 10x, move the window counts to Spark window functions.
    The filter itself is sd311_core.serving.history_filter_sql (tested in pandas form).
"""
from __future__ import annotations

import argparse


def main(argv=None):  # pragma: no cover - needs Databricks
    from pyspark.sql import SparkSession
    from sd311_core.serving import (
        build_daily_features,
        features_for_storage,
        history_filter_sql,
        prepare_requests,
    )

    p = argparse.ArgumentParser()
    p.add_argument("--catalog", default="sd311")
    p.add_argument("--snapshot-date", default=None, help="YYYY-MM-DD (default: latest snapshot)")
    args = p.parse_args(argv)
    c = args.catalog
    spark = SparkSession.builder.getOrCreate()

    snap = args.snapshot_date or str(spark.sql(
        f"SELECT MAX(first_seen_date) FROM {c}.silver.requests_first_seen").first()[0])
    print(f"features for snapshot_date={snap}")

    new_pdf = spark.sql(
        f"SELECT * FROM {c}.silver.requests_first_seen "
        f"WHERE first_seen_date = DATE '{snap}'").toPandas()
    hist_pdf = spark.sql(
        f"SELECT * FROM {c}.silver.requests_clean WHERE {history_filter_sql(snap)}").toPandas()
    print(f"new rows {len(new_pdf):,}  history rows {len(hist_pdf):,}")

    # 'closed' = prefer the history rows on dedupe
    feats = build_daily_features(prepare_requests(hist_pdf, "closed"),
                                 prepare_requests(new_pdf, "open"), snap)
    # Rows from the history win clean_requests' dedupe only if an id were in both;
    # build_daily_features removes new ids from the history first, so it never is.
    out = features_for_storage(feats)
    print(f"feature rows {len(out):,}")

    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {c}.ml")
    table = f"{c}.ml.features"
    if len(out) == 0:
        print("no new requests today; nothing written")
        return
    (spark.createDataFrame(out).write.format("delta").mode("overwrite")
     .option("replaceWhere", f"snapshot_date = DATE '{snap}'")   # idempotent re-runs
     .option("mergeSchema", "true").saveAsTable(table))
    print(f"wrote {len(out):,} rows to {table}")


if __name__ == "__main__":
    main()
