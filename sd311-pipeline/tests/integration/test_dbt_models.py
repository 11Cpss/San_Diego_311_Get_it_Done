"""Checks the two dbt models you write in lesson 7: requests_clean and ml_labels.

How it works: the test writes a TINY hand-made bronze dataset (two Parquet files,
about 25 rows) where every row exists to exercise one rule. It runs the real
`dbt build --target duckdb` on it, into a throwaway DuckDB file, and compares the
outputs with values you can check by hand (each row's comment says why).
A last test builds the committed 1% fixtures and compares row counts.

as_of_date = the latest open snapshot date = 2026-01-15 (the default).

Tests skip while a model still says "LESSON 7 STUB";
`python scripts/lessons.py check 7` runs them anyway (and fails on the stubs).
Needs dbt-duckdb (`pip install dbt-duckdb`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytestmark = pytest.mark.lesson7

DBT_DIR = Path(__file__).resolve().parents[2] / "dbt"
CLEAN = "dbt/models/silver/requests_clean.sql"
LABELS = "dbt/models/ml/ml_labels.sql"

COLUMNS = [
    "service_request_id", "service_request_parent_id", "date_requested", "case_age_days",
    "case_record_type", "service_name", "service_name_detail", "date_closed", "status",
    "lat", "lng", "zipcode", "council_district", "comm_plan_code", "comm_plan_name",
    "case_origin", "public_description", "snapshot_date", "source",
]


def req(id_, requested, *, status="In Process", closed="", service="Pothole", detail=None,
        age="5", district="3", plan="PACIFIC BEACH", origin="Mobile", description=None,
        snapshot="2026-01-15", source="open"):
    return {
        "service_request_id": id_, "service_request_parent_id": "", "date_requested": requested,
        "case_age_days": age, "case_record_type": "TSW", "service_name": service,
        "service_name_detail": detail, "date_closed": closed, "status": status,
        "lat": "32.7", "lng": "-117.1", "zipcode": "92109", "council_district": district,
        "comm_plan_code": "1", "comm_plan_name": plan, "case_origin": origin,
        "public_description": description, "snapshot_date": snapshot, "source": source,
    }


def closed(id_, requested, closed_on, **kw):
    kw.setdefault("source", "closed_2025")
    kw.setdefault("status", "Closed")
    return req(id_, requested, closed=closed_on, **kw)


OPEN_ROWS = [
    # 100: only in the OLDER open snapshot -> not in requests_clean (latest snapshot only)
    req("100", "2026-01-10T08:00:00", snapshot="2026-01-14"),
    # 101: in both snapshots, re-categorised in the latest one -> latest values win
    req("101", "2026-01-05T08:00:00", service="Pothole", snapshot="2026-01-14"),
    req("101", "2026-01-05T08:00:00", service="Pavement Maintenance"),
    # 102: open here AND in the closed file -> the closed row wins
    req("102", "2026-01-12T10:00:00"),
    # 103: open here, but CLOSED as Referred -> dropped (filter runs AFTER dedupe)
    req("103", "2026-01-11T10:00:00"),
    # 104: text cleaning; open 45 days at as_of -> '31+' censored
    req("104", "2025-12-01T07:15:00", service="Graffiti - Code Enforcement",
        plan="MID-CITY:CITY HEIGHTS", origin="", district=""),
    # 105: blank service name -> 'Unknown', no plan -> 'Unknown'; open exactly 31 days -> label
    req("105", "2025-12-15T23:59:00", service="", plan=None, origin=None),
    # 106: open exactly 30 days -> NO label yet
    req("106", "2025-12-16T00:01:00"),
    # 107: service 'Other' -> dropped
    req("107", "2026-01-02T08:00:00", service="Other"),
    # 108: negative case age -> dropped
    req("108", "2026-01-02T08:00:00", age="-3"),
    # 109: Referred -> dropped
    req("109", "2026-01-02T08:00:00", status="Referred"),
    # 110: unparseable case age (NULL after safe_cast) -> kept; filed today -> no label
    req("110", "2026-01-15T06:00:00", age="N/A", description="pothole on 5th"),
]

CLOSED_ROWS = [
    closed("102", "2026-01-12T10:00:00", "2026-01-14"),                       # 2 days  -> 0-2
    closed("103", "2026-01-11T10:00:00", "2026-01-13", status="Referred"),
    # 120: in two closed files -> the newer snapshot wins (detail NEW); 3 days -> 3-7
    closed("120", "2025-11-01T09:00:00", "2025-11-04", detail="OLD", snapshot="2026-01-10",
           source="backfill"),
    closed("120", "2025-11-01T09:00:00", "2025-11-04", detail="NEW"),
    closed("121", "2025-10-01T09:30:00", "2025-10-08"),                       # 7 days  -> 3-7
    closed("122", "2025-10-01T09:30:00", "2025-10-09"),                       # 8 days  -> 8-30
    closed("123", "2025-10-01T09:30:00", "2025-10-31"),                       # 30 days -> 8-30
    closed("124", "2025-10-01T09:30:00", "2025-11-01"),                       # 31 days -> 31+
    closed("125", "2025-10-01T09:30:00", "2025-10-01", description="abc"),    # 0 days  -> 0-2
    # 126: closed AFTER as_of, only 13 days old at as_of -> treated as open -> no label
    closed("126", "2026-01-02T09:00:00", "2026-01-20"),
    # 127: closed AFTER as_of, 56 days old at as_of -> '31+' censored, days_to_close NULL
    closed("127", "2025-11-20T09:00:00", "2026-01-20"),
    # 128: filed AFTER as_of -> in requests_clean, never labelled
    closed("128", "2026-01-16T09:00:00", "2026-01-17"),
    closed("129", "2025-09-01T09:00:00", "2025-09-02", service=None),         # 1 day -> 0-2
]

EXPECTED_CLEAN_IDS = {"101", "102", "104", "105", "106", "110",
                      "120", "121", "122", "123", "124", "125", "126", "127", "128", "129"}

# id -> (label_bucket, days_to_close, is_censored_31_plus) at as_of 2026-01-15
EXPECTED_LABELS = {
    "102": ("0-2", 2, False),
    "104": ("31+", None, True),
    "105": ("31+", None, True),
    "120": ("3-7", 3, False),
    "121": ("3-7", 7, False),
    "122": ("8-30", 8, False),
    "123": ("8-30", 30, False),
    "124": ("31+", 31, False),
    "125": ("0-2", 0, False),
    "127": ("31+", None, True),
    "129": ("0-2", 1, False),
}

# Same data with --vars as_of: 2025-10-31. Only requests filed by then count, and
# 124 (closed 2025-11-01, 30 days old on 2025-10-31) is still open -> no label.
EXPECTED_LABELS_OCT31 = {"121": "3-7", "122": "8-30", "123": "8-30", "125": "0-2", "129": "0-2"}

# Committed fixtures (dbt/fixtures/bronze, 1% sample), captured from the answer key.
FIXTURE_CLEAN_ROWS = 8284
FIXTURE_BUCKETS = {"0-2": 3452, "3-7": 1365, "8-30": 1262, "31+": 2072}
FIXTURE_CENSORED = 718


# ------------------------------------------------------------------ dbt runner
def write_bronze(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    ingested = datetime(2026, 1, 15, 14, 0)
    for name, rows in (("open_snapshots", OPEN_ROWS), ("closed_requests", CLOSED_ROWS)):
        cols = {c: pa.array([r[c] for r in rows], pa.string()) for c in COLUMNS}
        cols["_ingested_at"] = pa.array([ingested] * len(rows), pa.timestamp("us"))
        pq.write_table(pa.table(cols), folder / f"{name}.parquet")


class DbtRuns:
    """Runs dbt lazily (once per scenario) into throwaway DuckDB files."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.results: dict[str, dict] = {}

    def dbt(self, db: Path, args: list[str], variables: dict) -> None:
        exe = shutil.which("dbt")
        assert exe, "dbt is not on PATH: pip install dbt-duckdb"
        env = {**os.environ, "SD311_DUCKDB_PATH": str(db),
               "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
        cmd = [exe, *args, "--target", "duckdb", "--profiles-dir", ".",
               "--target-path", str(self.tmp / "target"), "--log-path", str(self.tmp / "logs"),
               "--vars", json.dumps(variables)]
        proc = subprocess.run(cmd, cwd=DBT_DIR, env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = "\n".join(proc.stdout.splitlines()[-40:])
            pytest.fail(f"`dbt {' '.join(args)}` failed:\n{tail}", pytrace=False)

    @staticmethod
    def query(db: Path, sql: str) -> list[tuple]:
        import duckdb

        con = duckdb.connect(str(db), read_only=True)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def handmade(self) -> dict:
        if "handmade" not in self.results:
            bronze, db = self.tmp / "bronze", self.tmp / "handmade.duckdb"
            write_bronze(bronze)
            self.dbt(db, ["build"], {"local_bronze_dir": str(bronze)})
            clean = self.query(db, "select * from silver.requests_clean")
            names = [r[0] for r in self.query(
                db, "select column_name from information_schema.columns "
                    "where table_schema = 'silver' and table_name = 'requests_clean' "
                    "order by ordinal_position")]
            labels = self.query(db, "select service_request_id, label_bucket, days_to_close, "
                                    "is_censored_31_plus, as_of_date, age_at_as_of "
                                    "from ml.ml_labels")
            self.dbt(db, ["run", "-s", "ml_labels"],
                     {"local_bronze_dir": str(bronze), "as_of": "2025-10-31"})
            oct31 = self.query(db, "select service_request_id, label_bucket, as_of_date "
                                   "from ml.ml_labels")
            self.results["handmade"] = {
                "clean": {row[0]: dict(zip(names, row, strict=True)) for row in clean},
                "clean_rows": len(clean),
                "labels": {r[0]: r for r in labels},
                "oct31": {r[0]: r for r in oct31},
            }
        return self.results["handmade"]

    def fixtures(self) -> dict:
        if "fixtures" not in self.results:
            db = self.tmp / "fixtures.duckdb"
            self.dbt(db, ["build"], {})
            self.results["fixtures"] = {
                "clean_rows": self.query(db, "select count(*) from silver.requests_clean")[0][0],
                "buckets": dict(self.query(
                    db, "select label_bucket, count(*) from ml.ml_labels group by 1")),
                "censored": self.query(
                    db, "select count(*) from ml.ml_labels where is_censored_31_plus")[0][0],
            }
        return self.results["fixtures"]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    return DbtRuns(tmp_path_factory.mktemp("dbt_lesson7"))


@pytest.fixture(autouse=True)
def need_dbt(request):
    if shutil.which("dbt") is None:
        if request.config.getoption("--lesson-strict"):
            pytest.fail("dbt is not on PATH: pip install dbt-duckdb")
        pytest.skip("dbt is not installed (pip install dbt-duckdb)")


@pytest.fixture
def clean(runs, skip_if_lesson_pending):
    skip_if_lesson_pending(CLEAN, 7)
    return runs.handmade()["clean"]


@pytest.fixture
def labels(runs, skip_if_lesson_pending):
    skip_if_lesson_pending(CLEAN, 7)
    skip_if_lesson_pending(LABELS, 7)
    return runs.handmade()


# ------------------------------------------------------------- requests_clean
def test_one_row_per_kept_request(clean, runs):
    assert set(clean) == EXPECTED_CLEAN_IDS
    assert runs.handmade()["clean_rows"] == len(EXPECTED_CLEAN_IDS), "duplicate ids"


def test_latest_open_snapshot_and_closed_wins(clean):
    assert clean["101"]["service_name"] == "Pavement Maintenance"   # latest snapshot
    assert clean["101"]["is_closed"] is False
    assert clean["102"]["is_closed"] is True                      # closed beats open
    assert clean["102"]["status"] == "Closed"
    assert str(clean["102"]["date_closed"]) == "2026-01-14"


def test_newest_closed_file_wins(clean):
    assert clean["120"]["service_name_detail"] == "NEW"
    assert str(clean["120"]["source_snapshot_date"]) == "2026-01-15"


def test_filters_run_after_dedupe(clean):
    # 103 was open (In Process) but closed as Referred: it must be dropped, not
    # fall back to its stale open row.
    assert "103" not in clean
    assert not {"107", "108", "109"} & set(clean)          # Other, negative age, Referred
    assert clean["110"]["case_age_days"] is None           # NULL age is kept


def test_text_normalisation(clean):
    assert clean["104"]["service_name"] == "Graffiti"
    assert clean["104"]["comm_plan_name"] == "Mid-city:city Heights"
    assert clean["104"]["case_origin"] == "Unknown"        # '' -> Unknown
    assert clean["104"]["council_district"] == 0           # blank -> NULL -> 0
    assert clean["105"]["service_name"] == "Unknown"       # '' -> Unknown
    assert clean["129"]["service_name"] == "Unknown"       # NULL -> Unknown
    assert clean["105"]["comm_plan_name"] == "Unknown"
    assert clean["105"]["case_origin"] == "Unknown"
    assert clean["101"]["comm_plan_name"] == "Pacific Beach"


def test_types_and_derived_columns(clean):
    assert str(clean["104"]["date_requested_day"]) == "2025-12-01"
    assert clean["104"]["council_district"] == 0 and clean["101"]["council_district"] == 3
    desc = (clean["110"]["desc_len"], clean["125"]["desc_len"], clean["101"]["desc_len"])
    assert desc == (14, 3, 0)


# ------------------------------------------------------------------ ml_labels
def test_exactly_the_final_labels(labels):
    got = {i: (b, d, c) for i, (_, b, d, c, _, _) in labels["labels"].items()}
    assert got == EXPECTED_LABELS


def test_young_open_and_future_requests_get_no_label(labels):
    # 101/110 open < 31 days, 106 open exactly 30 days, 126 closed after as_of
    # but young, 128 filed after as_of.
    assert not {"101", "106", "110", "126", "128"} & set(labels["labels"])


def test_as_of_date_and_age(labels):
    rows = labels["labels"]
    assert {str(r[4]) for r in rows.values()} == {"2026-01-15"}
    assert rows["105"][5] == 31 and rows["104"][5] == 45 and rows["127"][5] == 56


def test_as_of_var_moves_the_cutoff(labels):
    got = {i: b for i, (_, b, _) in labels["oct31"].items()}
    assert got == EXPECTED_LABELS_OCT31
    assert {str(r[2]) for r in labels["oct31"].values()} == {"2025-10-31"}


# --------------------------------------------- committed fixtures (regression)
def test_committed_fixture_counts(runs, skip_if_lesson_pending):
    skip_if_lesson_pending(CLEAN, 7)
    skip_if_lesson_pending(LABELS, 7)
    got = runs.fixtures()
    assert got["clean_rows"] == FIXTURE_CLEAN_ROWS
    assert got["buckets"] == FIXTURE_BUCKETS
    assert got["censored"] == FIXTURE_CENSORED
