"""Task 2 of sd311_daily: turn today's bronze rows into three silver tables.

    silver.requests_first_seen  insert-only. A request's fields AS FIRST SEEN,
                                plus desc_len, first_seen_date and seen_open.
    silver.field_changes        append-only. One row per tracked field whose value
                                changed since the previous day's state.
    silver.requests_current     MERGE upsert. The latest known state of each request.

Why bother? The public open-data files are OVERWRITTEN every day. If a request's
service_name or status is corrected after it was filed, the published file only
shows the corrected value. A model trained on those values would "see the future"
(leakage). Keeping what we saw on the first day lets features use only
information that existed when the request was filed.

How the code is organised (so it can be tested without Databricks):
    * Every step is a plain SQL string built by a small Python function.
    * The SQL uses only syntax that Spark SQL (Delta) and DuckDB both accept
      (explicit column lists, IS DISTINCT FROM, MERGE ... WHEN [NOT] MATCHED).
    * On Databricks, main() runs the strings with spark.sql().
    * databricks/tests/test_snapshots.py runs the SAME strings in DuckDB
      on tiny day-1 / day-2 / day-3 fixtures, and asks Spark's parser to parse them.

Run order inside one day (it matters):
    1. today_state view  - today's rows, one per id, closed beats open
    2. field_changes     - compare today_state with requests_current BEFORE it is updated
    3. first_seen insert - ids we have never seen before
    4. MERGE current     - upsert today's state

Re-running the same day is safe: step 2 finds no differences (current already
holds today's values) and skips (id, field, day) rows it already logged, step 3
finds no new ids, and step 4 rewrites the same values.
"""
from __future__ import annotations

import argparse
import datetime as dt

# Columns carried from bronze into silver. Kept as STRINGS on purpose: silver
# snapshots record exactly what the city published; typing happens in dbt.
SNAPSHOT_COLUMNS = [
    "service_request_id", "service_request_parent_id", "date_requested",
    "case_age_days", "case_record_type", "service_name", "service_name_detail",
    "date_closed", "status", "lat", "lng", "zipcode", "council_district",
    "comm_plan_code", "comm_plan_name", "case_origin",
]

# Columns DERIVED from bronze while building silver (typed, not strings).
#   desc_len = length of public_description, 0 if missing. The model uses it as a
#   feature; we keep only the length (not the free text), exactly like the slim
#   training files do (ml/scripts/make_slim.py). Without it, serving had to fill
#   desc_len = 0 for every request while training saw the real length (a
#   train/serve skew bug, see lessons/08).
DESC_LEN_SQL = "COALESCE(LENGTH(public_description), 0)"
DERIVED_COLUMNS = {"desc_len": "INT"}

# Fields whose changes we log in silver.field_changes.
# (Lesson 06 experiment: add one more here and predict what changes.)
TRACKED_FIELDS = [
    "service_name_detail", "case_record_type", "service_request_parent_id",
    "status", "service_name", "council_district",
]

DEFAULT_TABLES = {
    "bronze_open": "sd311.bronze.open_snapshots",
    "bronze_closed": "sd311.bronze.closed_requests",
    "first_seen": "sd311.silver.requests_first_seen",
    "current": "sd311.silver.requests_current",
    "changes": "sd311.silver.field_changes",
}


def _cols(prefix: str = "", cols: list[str] | None = None) -> str:
    """'a, b, c' or 's.a, s.b, s.c' - saves typing the column list everywhere."""
    cols = cols or SNAPSHOT_COLUMNS
    return ", ".join(f"{prefix}{c}" for c in cols)


# --------------------------------------------------------------------------- DDL
def ddl_statements(tables: dict = DEFAULT_TABLES, using: str = "USING DELTA") -> list[str]:
    """CREATE TABLE IF NOT EXISTS for the three silver tables.

    `using` is "USING DELTA" on Databricks and "" in the DuckDB tests.
    """
    string_cols = ",\n    ".join([f"{c} STRING" for c in SNAPSHOT_COLUMNS]
                                 + [f"{c} {t}" for c, t in DERIVED_COLUMNS.items()])
    return [
        f"""CREATE TABLE IF NOT EXISTS {tables['first_seen']} (
    {string_cols},
    first_seen_date DATE,
    seen_open BOOLEAN
) {using}""",
        f"""CREATE TABLE IF NOT EXISTS {tables['current']} (
    {string_cols},
    is_closed BOOLEAN,
    first_seen_date DATE,
    last_updated_date DATE
) {using}""",
        f"""CREATE TABLE IF NOT EXISTS {tables['changes']} (
    service_request_id STRING,
    field STRING,
    old_value STRING,
    new_value STRING,
    snapshot_date DATE
) {using}""",
    ]


# --------------------------------------------------------------- daily statements
def today_state_sql(snapshot_date: str, tables: dict = DEFAULT_TABLES) -> str:
    """One row per request for `snapshot_date`; a closed row beats an open row.

    seen_open_today records whether the id was in today's OPEN file at all, which
    first_seen needs even when the closed row wins.
    """
    return f"""CREATE OR REPLACE TEMP VIEW today_state AS
WITH unioned AS (
    SELECT {_cols()}, {DESC_LEN_SQL} AS desc_len, FALSE AS is_closed
    FROM {tables['bronze_open']}
    WHERE snapshot_date = '{snapshot_date}'
    UNION ALL
    SELECT {_cols()}, {DESC_LEN_SQL} AS desc_len, TRUE AS is_closed
    FROM {tables['bronze_closed']}
    WHERE snapshot_date = '{snapshot_date}'
),
ranked AS (
    SELECT
        *,
        MAX(CASE WHEN is_closed THEN 0 ELSE 1 END)
            OVER (PARTITION BY service_request_id) = 1                 AS seen_open_today,
        ROW_NUMBER() OVER (PARTITION BY service_request_id
                           ORDER BY CASE WHEN is_closed THEN 0 ELSE 1 END) AS rn_closed_first,
        ROW_NUMBER() OVER (PARTITION BY service_request_id
                           ORDER BY CASE WHEN is_closed THEN 1 ELSE 0 END) AS rn_open_first
    FROM unioned
)
SELECT * FROM ranked"""
    # Consumers pick rn_closed_first = 1 (latest truth) or rn_open_first = 1
    # (what was visible while the request was open, for first_seen).


def field_changes_sql(snapshot_date: str, tables: dict = DEFAULT_TABLES,
                      tracked: list[str] = TRACKED_FIELDS) -> str:
    """Append one row per (id, field) whose value differs from requests_current.

    LESSON 6: you write this. Return ONE SQL string:

        INSERT INTO {tables['changes']}
            (service_request_id, field, old_value, new_value, snapshot_date)
        SELECT ...

    Rules (each one has a test in databricks/tests/test_snapshots.py):
      * Compare today's state (the `today_state` view, rows with
        rn_closed_first = 1) with the current table, joined on
        service_request_id. Only ids already in requests_current can 'change'.
      * One candidate row per field in `tracked` (use the argument, not the
        constant): field name as a string literal, old value from current,
        new value from today. Hint: build one SELECT per field in Python and
        glue them with UNION ALL.
      * A change to or from NULL is a change. `<>` misses those; find the
        NULL-safe comparison operator that both Spark and DuckDB accept.
      * Closed wins: if current is closed and today's row is only open (a stale
        open file), that is NOT a change.
      * snapshot_date is `DATE '<snapshot_date>'`.
      * Retry safety: skip any (id, field, snapshot_date) already in the changes
        table (a crash after this INSERT but before the MERGE must not log the
        same diffs twice on retry). Hint: WHERE NOT EXISTS (...).
    Use only syntax Spark SQL and DuckDB both accept (look at first_seen_sql).
    """
    raise NotImplementedError(
        "[lesson 6] field_changes_sql: INSERT one row per changed tracked field "
        "(see the docstring)")


def first_seen_sql(snapshot_date: str, tables: dict = DEFAULT_TABLES) -> str:
    """Insert ids never seen before, with their fields AS FIRST SEEN.

    Prefer the open row (rn_open_first) so the stored fields are what a user
    could see while the request was open. Ids that only ever appear in a closed
    file (closed within a day, or loaded by backfill) get seen_open = FALSE.
    """
    return f"""INSERT INTO {tables['first_seen']} ({_cols()}, desc_len, first_seen_date, seen_open)
SELECT {_cols('s.')}, s.desc_len, DATE '{snapshot_date}', s.seen_open_today
FROM today_state s
WHERE s.rn_open_first = 1
  AND NOT EXISTS (
      SELECT 1 FROM {tables['first_seen']} f
      WHERE f.service_request_id = s.service_request_id
  )"""


def merge_current_sql(snapshot_date: str, tables: dict = DEFAULT_TABLES) -> str:
    """Upsert today's state into requests_current. Closed records win over open.

    LESSON 6: you write this. Return ONE SQL string:

        MERGE INTO {tables['current']} t
        USING (<today_state rows with rn_closed_first = 1>) s
        ON <same request>
        WHEN MATCHED AND <guard> THEN UPDATE SET ...
        WHEN NOT MATCHED THEN INSERT (...) VALUES (...)

    Rules:
      * MATCHED: update every SNAPSHOT_COLUMNS column except the key, plus
        desc_len and is_closed, and set last_updated_date to today. Leave
        first_seen_date alone (it is the day we first saw the request).
      * The guard: never let an open row overwrite a closed one
        (current closed AND today open -> no update).
      * NOT MATCHED: insert all SNAPSHOT_COLUMNS, desc_len, is_closed, and
        first_seen_date = last_updated_date = today.
      * Dates are `DATE '<snapshot_date>'`.
      * Write the column lists out (no `UPDATE SET *`; DuckDB and Spark differ
        there). `_cols()` and `_cols('s.')` build them for you.
    """
    raise NotImplementedError(
        "[lesson 6] merge_current_sql: MERGE today_state into requests_current, "
        "closed beats open (see the docstring)")


def daily_statements(snapshot_date: str, tables: dict = DEFAULT_TABLES) -> list[str]:
    """All statements for one snapshot day, in the order they must run."""
    dt.date.fromisoformat(snapshot_date)  # fail fast on a bad date (also blocks SQL injection)
    return [
        today_state_sql(snapshot_date, tables),
        field_changes_sql(snapshot_date, tables),
        first_seen_sql(snapshot_date, tables),
        merge_current_sql(snapshot_date, tables),
    ]


# ------------------------------------------------------------------ Databricks entry
def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs Databricks
    from pyspark.sql import SparkSession

    p = argparse.ArgumentParser()
    p.add_argument("--snapshot-date", default=None,
                   help="YYYY-MM-DD; default = latest snapshot_date in bronze.open_snapshots")
    p.add_argument("--catalog", default="sd311")
    args = p.parse_args(argv)

    spark = SparkSession.builder.getOrCreate()
    tables = {k: v.replace("sd311.", f"{args.catalog}.", 1) for k, v in DEFAULT_TABLES.items()}

    snapshot_date = args.snapshot_date or spark.sql(
        f"SELECT MAX(snapshot_date) FROM {tables['bronze_open']}").first()[0]
    if snapshot_date is None:
        raise SystemExit("bronze.open_snapshots is empty - did load_bronze run?")

    for stmt in ddl_statements(tables) + daily_statements(snapshot_date, tables):
        print(f"-- running:\n{stmt[:200]}...")
        spark.sql(stmt)

    for name in ("first_seen", "current", "changes"):
        n = spark.table(tables[name]).count()
        print(f"{tables[name]}: {n:,} rows")


if __name__ == "__main__":
    main()
