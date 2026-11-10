"""Tests for databricks/tasks/load_bronze.py.

COPY INTO and CREATE VOLUME are Databricks-only SQL, so we cannot execute them
locally. Instead we check the generated SQL for the properties that matter, and
parse the CREATE TABLE statements with open-source Spark's parser.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tasks"))
import load_bronze as lb  # noqa: E402


def test_setup_creates_all_schemas_and_volumes():
    stmts = lb.setup_statements("sd311")
    assert stmts[0] == "CREATE CATALOG IF NOT EXISTS sd311"
    for s in ("bronze", "silver", "gold", "ml"):
        assert f"CREATE SCHEMA IF NOT EXISTS sd311.{s}" in stmts
    assert "CREATE VOLUME IF NOT EXISTS sd311.bronze.landing" in stmts
    assert "CREATE VOLUME IF NOT EXISTS sd311.ml.run_summaries" in stmts
    # Everything must be re-runnable.
    assert all("IF NOT EXISTS" in s for s in stmts)


def test_skip_create_catalog():
    assert not any("CATALOG" in s for s in lb.setup_statements("dev_sd311", create_catalog=False))


def test_bronze_tables_are_all_strings_plus_metadata():
    sql = lb.create_table_sql("open_snapshots")
    for c in lb.SOURCE_COLUMNS:
        assert f"{c} STRING" in sql
    assert "_ingested_at TIMESTAMP" in sql and "_source_file STRING" in sql
    assert "USING DELTA" in sql


def test_copy_into_routes_sources_to_the_right_table():
    open_sql = lb.copy_into_sql("open_snapshots")
    closed_sql = lb.copy_into_sql("closed_requests")
    assert "PATTERN = 'open/snapshot_date=*/*.parquet'" in open_sql
    assert "PATTERN = '{closed_*,backfill}/snapshot_date=*/*.parquet'" in closed_sql
    for sql in (open_sql, closed_sql):
        assert "FROM '/Volumes/sd311/bronze/landing'" in sql
        assert "FILEFORMAT = PARQUET" in sql
        assert "_metadata.file_path AS _source_file" in sql
        # 'force' would disable COPY INTO's skip-already-loaded-files behaviour.
        assert "force" not in sql.lower()


def test_unknown_table_rejected():
    with pytest.raises(ValueError):
        lb.copy_into_sql("silver_stuff")


def test_statement_order():
    stmts = lb.all_statements()
    first_copy = next(i for i, s in enumerate(stmts) if s.startswith("COPY INTO"))
    last_create = max(i for i, s in enumerate(stmts) if s.startswith("CREATE"))
    assert last_create < first_copy  # tables exist before we copy into them


def test_create_table_parses_in_spark():
    pytest.importorskip("pyspark")
    from pyspark.sql import SparkSession

    spark = (SparkSession.builder.master("local[1]").appName("parse-check")
             .config("spark.ui.enabled", "false").getOrCreate())
    parser = spark._jsparkSession.sessionState().sqlParser()
    for t in lb.TARGETS:
        parser.parsePlan(lb.create_table_sql(t))
