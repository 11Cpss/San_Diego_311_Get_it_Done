import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tasks"))
import evaluate_and_summarize as ev  # noqa: E402

# Required fields from docs/run_summary_schema.md (v1)
REQUIRED = {"schema_version", "run_date", "snapshot_date", "generated_at", "status"}


def test_summary_path_matches_contract():
    assert ev.summary_path("2026-10-08") == (
        "/Volumes/sd311/ml/run_summaries/date=2026-10-08/run_summary.json")


def test_build_and_write_summary(tmp_path):
    now = dt.datetime(2026, 10, 8, 21, 0, tzinfo=dt.UTC)
    counts = {"bronze.open_snapshots": 1234, "silver.requests_current": 10}
    s = ev.build_run_summary("2026-10-08", counts, job_run_id=777, freshness_hours=9.5, now=now)
    assert REQUIRED <= set(s)
    assert s["schema_version"] == 1 and s["run_date"] == s["snapshot_date"] == "2026-10-08"
    assert s["status"] == "stub" and s["generated_at"] == "2026-10-08T21:00:00Z"
    assert s["rows_ingested"] == 1234            # monitor's RowsIngested metric
    assert s["model"] is None and s["drift"] is None and s["data_quality_failures"] is None
    out = tmp_path / "date=2026-10-08" / "run_summary.json"
    ev.write_summary(s, str(out))
    assert json.loads(out.read_text())["job_run_id"] == 777


def test_missing_count_marks_failed():
    s = ev.build_run_summary("2026-10-08", {"gold.fct_requests": None})
    assert s["status"] == "failed"
    assert s["rows_ingested"] is None


# ---------------------------------------------------------------- timezone fix
def test_freshness_converts_pacific_local_time_to_utc():
    # 2026-10-08 06:00 Pacific (PDT, UTC-7) = 13:00 UTC. "now" = 15:30 UTC -> 2.5 h, not 9.5 h.
    now = dt.datetime(2026, 10, 8, 15, 30, tzinfo=dt.UTC)
    assert ev.freshness_hours(dt.datetime(2026, 10, 8, 6, 0), now) == 2.5
    # winter (PST, UTC-8): 2026-01-14 06:00 local = 14:00 UTC
    winter_now = dt.datetime(2026, 1, 14, 15, 0, tzinfo=dt.UTC)
    assert ev.freshness_hours(dt.datetime(2026, 1, 14, 6, 0), winter_now) == 1.0
    assert ev.freshness_hours(None, now) is None


def test_folder_date_is_pacific_date():
    # 03:30 UTC on Oct 9 is still the evening of Oct 8 in San Diego.
    assert ev.pacific_date(dt.datetime(2026, 10, 9, 3, 30, tzinfo=dt.UTC)) == "2026-10-08"
    assert ev.pacific_date(dt.datetime(2026, 10, 8, 14, 0, tzinfo=dt.UTC)) == "2026-10-08"


# ---------------------------------------------------------------- full summary
def test_count_dq_failures():
    assert ev.count_dq_failures({"a": True, "b": False, "c": None}) == 1
    assert ev.count_dq_failures({"a": True}) == 0
    assert ev.count_dq_failures({"a": None}) is None          # nothing ran -> not measured
    assert set(ev.DQ_CHECKS) >= {"predictions_cover_features", "probabilities_sum_to_one"}


def test_full_summary_matches_schema_example():
    """Field names/types the monitor Lambda reads (docs/run_summary_schema.md)."""
    now = dt.datetime(2026, 10, 8, 14, 10, tzinfo=dt.UTC)
    model = {"version": "3", "metrics": {"macro_f1": 0.61, "log_loss": 1.2}, "n_evaluated": 900}
    drift = {"max_psi": 0.05, "psi_by_feature": {"service_name": 0.05, "case_origin": 0.01}}
    s = ev.build_run_summary("2026-10-08", {"bronze.open_snapshots": 1234}, job_run_id=777,
                             freshness_hours=9.5, model=model, now=now,
                             data_quality_failures=0, drift=drift, run_date="2026-10-08")
    assert s["status"] == "ok" and s["notes"] == []
    assert s["model"]["metrics"]["macro_f1"] == 0.61 and s["model"]["version"] == "3"
    assert s["drift"]["max_psi"] == 0.05 and s["data_quality_failures"] == 0
    assert s["freshness_hours"] == 9.5 and s["rows_ingested"] == 1234
    json.dumps(s)  # serialisable


def test_summary_is_read_by_the_monitor():
    """Round trip through the real AWS-side reader (src/sd311/monitor.py)."""
    import pytest
    monitor = pytest.importorskip("sd311.monitor")   # needs boto3 (installed by `pip install -e .`)

    s = ev.build_run_summary("2026-10-08", {"bronze.open_snapshots": 5},
                             model={"version": "1", "metrics": {"macro_f1": 0.5, "log_loss": None}},
                             data_quality_failures=2, drift={"max_psi": 0.3, "psi_by_feature": {}},
                             freshness_hours=3.0)
    summary, warnings = monitor.parse_summary(json.dumps(s).encode())
    assert warnings == []
    got = {m["MetricName"]: m["Value"] for m in monitor.metrics_from_summary(summary)}
    assert got == {"JobMissing": 0.0, "RowsIngested": 5.0, "DataQualityFailures": 2.0,
                   "FreshnessHours": 3.0, "LiveMacroF1": 0.5, "MaxPSI": 0.3}
    # (a null log_loss is skipped, not sent as a metric)
