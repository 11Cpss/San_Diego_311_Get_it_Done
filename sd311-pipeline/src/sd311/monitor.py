"""Lambda `monitor`: Databricks run_summary.json -> CloudWatch metrics.

Runs at 08:00 Pacific, two hours after ingest. It reads
    /Volumes/sd311/ml/run_summaries/date=YYYY-MM-DD/run_summary.json
through the Databricks Files API and publishes custom metrics in the
`SD311` namespace. CloudWatch alarms (infra/main.tf) watch those metrics.

If the file does not exist, the Databricks job did not finish (or never
ran) -> JobMissing=1. The schema is documented in docs/run_summary_schema.md.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import boto3

from sd311.config import Settings, load_settings
from sd311.ingest import DatabricksClient, get_databricks_secrets, http_session, today_local
from sd311.logging_utils import log_step

SUPPORTED_SCHEMA_VERSIONS = {1}



def _rows_ingested(s: dict):
    """Explicit field first; else the bronze open-snapshot row count."""
    if s.get("rows_ingested") is not None:
        return s["rows_ingested"]
    return (s.get("row_counts") or {}).get("bronze.open_snapshots")


def _model_metric(name: str):
    return lambda s: ((s.get("model") or {}).get("metrics") or {}).get(name)


# metric name -> (function that pulls the value out of the summary, unit)
# Field names follow docs/run_summary_schema.md (version 1).
METRICS = {
    "RowsIngested": (_rows_ingested, "Count"),
    "DataQualityFailures": (lambda s: s.get("data_quality_failures"), "Count"),
    "FreshnessHours": (lambda s: s.get("freshness_hours"), "None"),
    "LiveMacroF1": (_model_metric("macro_f1"), "None"),
    "LogLoss": (_model_metric("log_loss"), "None"),
    "MaxPSI": (lambda s: (s.get("drift") or {}).get("max_psi"), "None"),
}


def summary_path(summary_root: str, run_date: str) -> str:
    return f"{summary_root}/date={run_date}/run_summary.json"


def metrics_from_summary(summary: dict | None) -> list[dict]:
    """Turn a summary (or None if missing) into CloudWatch MetricData items.

    Null / absent values are *skipped*, not sent as 0: a missing F1 should
    look like "no data" to the alarm, not like "the model is terrible".
    """
    if summary is None:
        return [{"MetricName": "JobMissing", "Value": 1.0, "Unit": "Count"}]

    data = [{"MetricName": "JobMissing", "Value": 0.0, "Unit": "Count"}]
    for name, (getter, unit) in METRICS.items():
        value = getter(summary)
        if value is None:
            continue
        data.append({"MetricName": name, "Value": float(value), "Unit": unit})
    return data


def parse_summary(raw: bytes | None) -> tuple[dict | None, list[str]]:
    """Parse + minimally check the JSON. Returns (summary_or_None, warnings)."""
    if raw is None:
        return None, ["run_summary.json not found"]
    try:
        summary = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON: {exc}"]
    warnings = []
    if summary.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        warnings.append(f"unexpected schema_version {summary.get('schema_version')!r}")
    if summary.get("status") not in ("ok", "stub"):
        warnings.append(f"job status is {summary.get('status')!r}")
    return summary, warnings


def handler(event, context, *, settings: Settings | None = None, session=None,
            ssm=None, cw=None) -> dict:
    """Lambda entry point. Optional event: {"date": "YYYY-MM-DD"}."""
    settings = settings or load_settings()
    ssm = ssm or boto3.client("ssm")
    cw = cw or boto3.client("cloudwatch")
    session = session or http_session(settings)
    event = event or {}

    run_date = event.get("date") or today_local(settings.timezone).isoformat()
    path = summary_path(settings.summary_root, run_date)

    secrets = get_databricks_secrets(ssm, settings.ssm_prefix)
    dbx = DatabricksClient(secrets["host"], secrets["token"], session,
                           timeout=settings.http_timeout_s)
    raw = dbx.download_file(path)
    summary, warnings = parse_summary(raw)
    log_step("read_summary", path=path, found=summary is not None, warnings=warnings,
             level="WARNING" if warnings else "INFO")

    data = metrics_from_summary(summary)
    ts = datetime.now(UTC)
    for d in data:
        d["Timestamp"] = ts
    cw.put_metric_data(Namespace=settings.metric_namespace, MetricData=data)
    published = {d["MetricName"]: d["Value"] for d in data}
    log_step("publish_metrics", namespace=settings.metric_namespace, metrics=published)
    return {"date": run_date, "found": summary is not None, "metrics": published,
            "warnings": warnings}
