import json

import boto3
import pytest
import responses
from moto import mock_aws

from sd311 import monitor
from sd311.config import Settings
from sd311.ingest import http_session

HOST = "https://dbc-test.cloud.databricks.com"
DATE = "2026-10-08"
URL = (f"{HOST}/api/2.0/fs/files/Volumes/sd311/ml/run_summaries/"
       f"date={DATE}/run_summary.json")

# Matches the example in docs/run_summary_schema.md
SUMMARY = {
    "schema_version": 1, "run_date": DATE, "snapshot_date": DATE, "status": "ok",
    "generated_at": "2026-10-08T14:10:00Z", "job_run_id": 777,
    "rows_ingested": 1234, "data_quality_failures": 0, "freshness_hours": 9.5,
    "row_counts": {"bronze.open_snapshots": 1234},
    "model": {"version": "3", "metrics": {"macro_f1": 0.61, "log_loss": 1.2}},
    "drift": {"max_psi": 0.05, "psi_by_feature": {"service_name": 0.05}},
    "notes": [],
}


def test_metrics_from_missing_summary():
    assert monitor.metrics_from_summary(None) == [
        {"MetricName": "JobMissing", "Value": 1.0, "Unit": "Count"}]


def test_null_values_are_skipped_not_zeroed():
    s = dict(SUMMARY, model={"version": "3", "metrics": {"macro_f1": None, "log_loss": 0.9}})
    names = {d["MetricName"] for d in monitor.metrics_from_summary(s)}
    assert "LiveMacroF1" not in names and "LogLoss" in names


def test_databricks_stub_summary_is_understood():
    """The current Databricks stub: no model yet, row counts only."""
    stub = {"schema_version": 1, "run_date": DATE, "status": "stub", "model": None,
            "row_counts": {"bronze.open_snapshots": 50}, "notes": ["stub"]}
    s, warnings = monitor.parse_summary(json.dumps(stub).encode())
    assert warnings == []
    got = {d["MetricName"]: d["Value"] for d in monitor.metrics_from_summary(s)}
    assert got == {"JobMissing": 0.0, "RowsIngested": 50.0}


def test_failed_status_warns():
    _, warnings = monitor.parse_summary(json.dumps(dict(SUMMARY, status="failed")).encode())
    assert any("failed" in w for w in warnings)


def test_parse_summary_warns_on_bad_version():
    s, warnings = monitor.parse_summary(json.dumps({"schema_version": 99}).encode())
    assert s is not None and warnings


def test_parse_summary_bad_json():
    s, warnings = monitor.parse_summary(b"{nope")
    assert s is None and "invalid JSON" in warnings[0]


@pytest.fixture
def aws():
    with mock_aws():
        ssm = boto3.client("ssm", region_name="us-west-2")
        for name, value in [("host", HOST), ("token", "t"), ("daily_job_id", "1")]:
            ssm.put_parameter(Name=f"/sd311/databricks/{name}", Value=value,
                              Type="SecureString")
        yield ssm, boto3.client("cloudwatch", region_name="us-west-2")


def _run(aws):
    ssm, cw = aws
    st = Settings(bucket="unused", http_retries=0)
    return monitor.handler({"date": DATE}, None, settings=st, ssm=ssm, cw=cw,
                           session=http_session(st))


@responses.activate
def test_handler_publishes_all_metrics(aws):
    responses.add(responses.GET, URL, body=json.dumps(SUMMARY))
    out = _run(aws)
    assert out["found"] is True
    assert out["metrics"] == {"JobMissing": 0.0, "RowsIngested": 1234.0,
                              "DataQualityFailures": 0.0, "FreshnessHours": 9.5,
                              "LiveMacroF1": 0.61, "LogLoss": 1.2, "MaxPSI": 0.05}
    listed = {m["MetricName"] for m in aws[1].list_metrics(Namespace="SD311")["Metrics"]}
    assert listed == set(out["metrics"])


@responses.activate
def test_handler_missing_summary_sets_job_missing(aws):
    responses.add(responses.GET, URL, status=404, json={"error_code": "NOT_FOUND"})
    out = _run(aws)
    assert out["found"] is False
    assert out["metrics"] == {"JobMissing": 1.0}
