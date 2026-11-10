#!/usr/bin/env bash
# Build + test every dbt model on open-source Spark SQL (in-process), as a
# dialect check for Databricks. Needs: pyspark, dbt-spark[session], Java 17+.
# Usage (from repo root or dbt/):  bash dbt/scripts/run_spark_local.sh
set -euo pipefail
DBT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$DBT_DIR/local_data/spark"
mkdir -p "$WORK" && cd "$WORK"          # Spark's metastore_db lives here
# Full local data if you generated it, else the committed 1% fixture.
BRONZE="$DBT_DIR/local_data/bronze"; [ -d "$BRONZE" ] || BRONZE="$DBT_DIR/fixtures/bronze"
python "$DBT_DIR/scripts/spark_local_setup.py" --bronze "$BRONZE"
DO_NOT_TRACK=1 dbt build --project-dir "$DBT_DIR" --profiles-dir "$DBT_DIR" --target spark_local \
  --target-path "$WORK/target" --log-path "$WORK/logs"
