"""Tests for databricks/tasks/snapshots.py.

Delta Lake's Spark jars could not be downloaded in the build sandbox (Maven is
blocked), so these tests:
  1. EXECUTE the exact SQL strings from snapshots.py in DuckDB (which supports
     MERGE INTO since 1.4), on a tiny 3-day story, and check the results;
  2. PARSE the same strings with Spark's SQL parser (if pyspark is installed)
     to catch Spark-only syntax errors.

Story (3 requests over 3 days):
    id 1  day1 open (New)  -> day2 status In Process + detail filled -> day3 closed
    id 2  day2 open        -> day3 open, unchanged
    id 3  never seen open; appears in the closed file on day3 (closed same day)
    id 1  ALSO appears in day3's open file (stale) - closed must still win.

Run:  pytest databricks/tests -q

Lesson 6: you write `field_changes_sql` and `merge_current_sql`. Every test that
runs the daily statements is marked `lesson6` and skips until both are written;
`python scripts/lessons.py check 6` runs them strictly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tasks"))
import snapshots as snap  # noqa: E402

COLS = snap.SNAPSHOT_COLUMNS
# Bronze also has public_description (free text). Silver keeps only its length.
BRONZE_EXTRA = ["public_description"]


def row(id_, status="New", detail=None, service="Pothole", district="3",
        date_closed=None, snapshot_date="2026-01-01", description=None):
    r = {c: None for c in COLS + BRONZE_EXTRA}
    r["public_description"] = description
    r.update(service_request_id=str(id_), status=status, service_name_detail=detail,
             service_name=service, council_district=district, date_closed=date_closed,
             date_requested="2026-01-01T09:00:00", case_record_type="TSW")
    r["snapshot_date"] = snapshot_date
    return r


DAY1 = "2026-01-01"
DAY2 = "2026-01-02"
DAY3 = "2026-01-03"

OPEN = {
    DAY1: [row(1, snapshot_date=DAY1, description="hole on 5th")],          # 11 chars
    DAY2: [row(1, status="In Process", detail="DEEP POTHOLE", snapshot_date=DAY2,
               description="hole on 5th ave"),                                # edited: 15 chars
           row(2, service="Graffiti - Public", district="5", snapshot_date=DAY2)],
    DAY3: [row(1, status="In Process", detail="DEEP POTHOLE", snapshot_date=DAY3),  # stale
           row(2, service="Graffiti - Public", district="5", snapshot_date=DAY3)],
}
CLOSED = {
    DAY1: [],
    DAY2: [],
    DAY3: [row(1, status="Closed", detail="DEEP POTHOLE", date_closed="2026-01-03",
               snapshot_date=DAY3, description="hole on 5th ave"),
           row(3, status="Closed", service="Street Light Maintenance",
               date_closed="2026-01-03", snapshot_date=DAY3)],
}

TABLES = {
    "bronze_open": "sd311.bronze.open_snapshots",
    "bronze_closed": "sd311.bronze.closed_requests",
    "first_seen": "sd311.silver.requests_first_seen",
    "current": "sd311.silver.requests_current",
    "changes": "sd311.silver.field_changes",
}


def make_db() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB laid out like Unity Catalog: catalog sd311, schemas bronze/silver."""
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS sd311")
    con.execute("CREATE SCHEMA sd311.bronze")
    con.execute("CREATE SCHEMA sd311.silver")
    cols = ", ".join(f"{c} VARCHAR" for c in COLS + BRONZE_EXTRA + ["snapshot_date"])
    con.execute(f"CREATE TABLE {TABLES['bronze_open']} ({cols})")
    con.execute(f"CREATE TABLE {TABLES['bronze_closed']} ({cols})")
    for stmt in snap.ddl_statements(TABLES, using=""):
        con.execute(stmt)
    return con


def insert_rows(con, table, rows):
    for r in rows:
        keys = list(r)
        placeholders = ", ".join("?" for _ in keys)
        con.execute(f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})",
                    [r[k] for k in keys])


def load_day(con, day):
    """Simulate load_bronze: append that day's files to the bronze tables."""
    insert_rows(con, TABLES["bronze_open"], OPEN[day])
    insert_rows(con, TABLES["bronze_closed"], CLOSED[day])


def run_day(con, day):
    load_day(con, day)
    for stmt in snap.daily_statements(day, TABLES):
        con.execute(stmt)


def as_dicts(con, sql):
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


@pytest.fixture()
def con_after_3_days():
    con = make_db()
    for d in (DAY1, DAY2, DAY3):
        run_day(con, d)
    return con


# ---------------------------------------------------------------- behaviour tests
@pytest.mark.lesson6
def test_new_request_is_inserted_once_with_first_seen_fields(con_after_3_days):
    fs = as_dicts(con_after_3_days,
                  f"SELECT * FROM {TABLES['first_seen']} ORDER BY service_request_id")
    assert [r["service_request_id"] for r in fs] == ["1", "2", "3"]
    one = fs[0]
    # Fields as first seen on day 1, NOT the later corrected values (no leakage).
    assert one["status"] == "New"
    assert one["service_name_detail"] is None
    assert str(one["first_seen_date"]) == DAY1
    assert one["seen_open"] is True
    assert str(fs[1]["first_seen_date"]) == DAY2


@pytest.mark.lesson6
def test_request_never_seen_open_gets_seen_open_false(con_after_3_days):
    three = as_dicts(con_after_3_days,
                     f"SELECT * FROM {TABLES['first_seen']} WHERE service_request_id = '3'")[0]
    assert three["seen_open"] is False
    assert three["status"] == "Closed"
    assert str(three["first_seen_date"]) == DAY3


@pytest.mark.lesson6
def test_desc_len_is_derived_from_public_description(con_after_3_days):
    """desc_len = LENGTH(public_description), 0 when missing.

    first_seen keeps the length AS FIRST SEEN (11), current keeps the latest one.
    This is what the daily features read, so serving sees the same desc_len
    the model was trained on (training uses the length from the slim files).
    """
    fs = {r["service_request_id"]: r["desc_len"]
          for r in as_dicts(con_after_3_days, f"SELECT * FROM {TABLES['first_seen']}")}
    assert fs == {"1": 11, "2": 0, "3": 0}
    cur = {r["service_request_id"]: r["desc_len"]
           for r in as_dicts(con_after_3_days, f"SELECT * FROM {TABLES['current']}")}
    assert cur["2"] == 0 and cur["3"] == 0
    assert cur["1"] == 15   # latest state: the edited description


@pytest.mark.lesson6
def test_field_changes_are_logged(con_after_3_days):
    ch = as_dicts(con_after_3_days,
                  f"SELECT service_request_id, field, old_value, new_value, snapshot_date "
                  f"FROM {TABLES['changes']} ORDER BY snapshot_date, field")
    got = {(r["service_request_id"], r["field"], r["old_value"], r["new_value"],
            str(r["snapshot_date"])) for r in ch}
    assert got == {
        ("1", "service_name_detail", None, "DEEP POTHOLE", DAY2),
        ("1", "status", "New", "In Process", DAY2),
        ("1", "status", "In Process", "Closed", DAY3),
    }
    # id 2 never changed; id 3 was new on day 3, so neither has change rows.


@pytest.mark.lesson6
def test_request_closes_and_closed_beats_stale_open(con_after_3_days):
    cur = {r["service_request_id"]: r
           for r in as_dicts(con_after_3_days, f"SELECT * FROM {TABLES['current']}")}
    assert set(cur) == {"1", "2", "3"}
    assert cur["1"]["is_closed"] is True and cur["1"]["status"] == "Closed"
    assert cur["1"]["date_closed"] == "2026-01-03"
    assert str(cur["1"]["first_seen_date"]) == DAY1
    assert cur["2"]["is_closed"] is False
    assert cur["3"]["is_closed"] is True


@pytest.mark.lesson6
def test_stale_open_row_after_close_does_not_reopen():
    """Day 4: only an old open row for id 1 shows up. requests_current must stay closed
    and no bogus 'Closed -> In Process' change may be logged."""
    con = make_db()
    for d in (DAY1, DAY2, DAY3):
        run_day(con, d)
    day4 = "2026-01-04"
    OPEN[day4] = [row(1, status="In Process", detail="DEEP POTHOLE", snapshot_date=day4)]
    CLOSED[day4] = []
    try:
        run_day(con, day4)
    finally:
        OPEN.pop(day4), CLOSED.pop(day4)
    one = as_dicts(con, f"SELECT * FROM {TABLES['current']} WHERE service_request_id = '1'")[0]
    assert one["status"] == "Closed"
    n = con.execute(f"SELECT COUNT(*) FROM {TABLES['changes']} "
                    f"WHERE snapshot_date = DATE '{day4}'").fetchone()[0]
    assert n == 0


@pytest.mark.lesson6
def test_rerunning_a_day_is_idempotent(con_after_3_days):
    con = con_after_3_days

    def counts():
        return [con.execute(f"SELECT COUNT(*) FROM {TABLES[t]}").fetchone()[0]
                for t in ("first_seen", "current", "changes")]

    before = counts()
    for stmt in snap.daily_statements(DAY3, TABLES):  # same day again, no new bronze rows
        con.execute(stmt)
    assert counts() == before


@pytest.mark.lesson6
def test_retry_after_crash_before_merge_does_not_duplicate_changes():
    """Simulate a crash after field_changes was written but before the MERGE ran:
    the retry finds the same diffs again but must not log them twice."""
    con = make_db()
    run_day(con, DAY1)
    load_day(con, DAY2)
    view, changes, first_seen, merge = snap.daily_statements(DAY2, TABLES)
    con.execute(view)
    con.execute(changes)            # attempt 1 ... crash here
    for stmt in (view, changes, first_seen, merge):   # attempt 2 (full retry)
        con.execute(stmt)
    n = con.execute(f"SELECT COUNT(*) FROM {TABLES['changes']}").fetchone()[0]
    assert n == 2   # status + service_name_detail for id 1, once each


@pytest.mark.lesson6
def test_merge_sets_last_updated_but_keeps_first_seen_date(con_after_3_days):
    cur = {r["service_request_id"]: r
           for r in as_dicts(con_after_3_days, f"SELECT * FROM {TABLES['current']}")}
    # (first_seen_date, last_updated_date): first seen on day N, updated by every later day.
    assert (str(cur["1"]["first_seen_date"]), str(cur["1"]["last_updated_date"])) == (DAY1, DAY3)
    assert (str(cur["2"]["first_seen_date"]), str(cur["2"]["last_updated_date"])) == (DAY2, DAY3)
    assert (str(cur["3"]["first_seen_date"]), str(cur["3"]["last_updated_date"])) == (DAY3, DAY3)


@pytest.mark.lesson6
def test_change_to_null_is_logged_and_only_tracked_fields_count():
    """id 9: detail 'CURB' -> NULL and district 3 -> 4 on day 2.

    With tracked=["service_name_detail"], exactly one change row is expected:
    the value -> NULL change (a plain `<>` would miss it), and nothing for the
    untracked district.
    """
    con = make_db()
    day1 = [row(9, detail="CURB", district="3", snapshot_date=DAY1)]
    day2 = [row(9, detail=None, district="4", snapshot_date=DAY2)]
    for day, rows in ((DAY1, day1), (DAY2, day2)):
        insert_rows(con, TABLES["bronze_open"], rows)
        con.execute(snap.today_state_sql(day, TABLES))
        con.execute(snap.field_changes_sql(day, TABLES, tracked=["service_name_detail"]))
        con.execute(snap.first_seen_sql(day, TABLES))
        con.execute(snap.merge_current_sql(day, TABLES))
    got = con.execute(f"SELECT service_request_id, field, old_value, new_value, snapshot_date "
                      f"FROM {TABLES['changes']}").fetchall()
    assert [(i, f, o, n, str(d)) for i, f, o, n, d in got] == [
        ("9", "service_name_detail", "CURB", None, DAY2)]
    district = con.execute(f"SELECT council_district FROM {TABLES['current']}").fetchone()[0]
    assert district == "4"   # the MERGE still updates untracked columns


def test_bad_snapshot_date_is_rejected():
    with pytest.raises(ValueError):
        snap.daily_statements("2026-01-01'; DROP TABLE x; --", TABLES)


# ------------------------------------------------------------- Spark syntax check
@pytest.mark.lesson6
def test_sql_parses_in_spark():
    """Parse (not execute) every statement with Spark's own SQL parser.

    Execution needs Delta jars, which were not downloadable in the sandbox.
    """
    pytest.importorskip("pyspark")
    from pyspark.sql import SparkSession

    spark = (SparkSession.builder.master("local[1]").appName("parse-check")
             .config("spark.ui.enabled", "false").getOrCreate())
    parser = spark._jsparkSession.sessionState().sqlParser()
    for stmt in snap.ddl_statements() + snap.daily_statements(DAY1):
        parser.parsePlan(stmt)  # raises ParseException on bad syntax
