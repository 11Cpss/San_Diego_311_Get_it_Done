"""Task 6 (last) of sd311_daily: evaluate the model and write run_summary.json.

Output path (a Unity Catalog volume, read by the AWS monitor Lambda):
    /Volumes/<catalog>/ml/run_summaries/date=YYYY-MM-DD/run_summary.json
YYYY-MM-DD is the America/Los_Angeles date of the run (normally = snapshot_date).

Schema: docs/run_summary_schema.md (owned by the AWS side, version 1). Key points:
  * null means "not measured" - the monitor skips nulls instead of publishing 0;
  * status "stub" = no champion model yet (valid v1), "failed" = a table could not be counted.

What is measured (pure functions below and in sd311_core.monitoring, all unit-tested):
  * row_counts / rows_ingested   simple Spark counts
  * freshness_hours              now (UTC) - newest date_requested. City timestamps are
                                 America/Los_Angeles wall-clock time with no offset, so
                                 they are localised to Pacific and converted to UTC first.
  * data_quality_failures        number of failed post-scoring checks (DQ_CHECKS). dbt test
                                 failures already stop the job at dbt_build, so they never
                                 reach this task (the missing file then raises JobMissing).
  * model.metrics                macro-F1 and log-loss of predictions whose labels became
                                 final in the last 28 days (sd311_core.monitoring.live_metrics).
                                 A label can take up to 31 days to become final, so these
                                 metrics describe predictions made roughly 1-2 months ago.
  * drift                        PSI of service_name and case_origin shares, requests first
                                 seen in the last 7 days vs the champion's training reference
                                 (reference_distributions.json logged by the train job).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

SCHEMA_VERSION = 1

# table -> optional WHERE clause (placeholders filled with the snapshot date)
COUNTED_TABLES = {
    "bronze.open_snapshots": "snapshot_date = '{d}'",
    "bronze.closed_requests": None,
    "silver.requests_current": None,
    "silver.requests_first_seen": "first_seen_date = DATE '{d}'",
    "silver.field_changes": "snapshot_date = DATE '{d}'",
    "gold.fct_requests": None,
    "ml.ml_labels": None,
}


def summary_path(run_date: str, catalog: str = "sd311") -> str:
    dt.date.fromisoformat(run_date)
    return f"/Volumes/{catalog}/ml/run_summaries/date={run_date}/run_summary.json"


PACIFIC = "America/Los_Angeles"


def pacific_date(now_utc: dt.datetime) -> str:
    """Calendar date in San Diego for a UTC instant (names the summary folder)."""
    from zoneinfo import ZoneInfo
    return now_utc.astimezone(ZoneInfo(PACIFIC)).date().isoformat()


def freshness_hours(newest_local: dt.datetime | None, now_utc: dt.datetime) -> float | None:
    """Hours between now and the newest request.

    ``newest_local`` is a naive timestamp as the city publishes it, i.e. Pacific
    wall-clock time. Localise it to America/Los_Angeles (DST-aware), convert to
    UTC, then subtract. Comparing it directly with UTC "now" would be off by 7-8 h.
    """
    from zoneinfo import ZoneInfo
    if newest_local is None:
        return None
    if newest_local.tzinfo is None:
        newest_local = newest_local.replace(tzinfo=ZoneInfo(PACIFIC))
    delta = now_utc - newest_local.astimezone(dt.UTC)
    return round(delta.total_seconds() / 3600, 2)


# name -> human description; main() computes one boolean per name (True = passed)
DQ_CHECKS = {
    "predictions_cover_features": "every feature row for today has a prediction",
    "prediction_ids_not_null": "no null service_request_id in today's predictions",
    "probabilities_sum_to_one": "p_0_2+p_3_7+p_8_30+p_31_plus within 1e-3 of 1",
    "current_ids_unique": "service_request_id is unique in silver.requests_current",
}


def count_dq_failures(results: dict) -> int | None:
    """results: {check: True/False/None}. None = could not run (not counted). All None -> None."""
    ran = [v for v in results.values() if v is not None]
    if not ran:
        return None
    return sum(1 for v in ran if not v)


def build_run_summary(snapshot_date: str, row_counts: dict,
                      job_run_id: int | None = None,
                      freshness_hours: float | None = None,
                      model: dict | None = None, notes: list | None = None,
                      now: dt.datetime | None = None,
                      data_quality_failures: int | None = None,
                      drift: dict | None = None,
                      run_date: str | None = None) -> dict:
    """Pure function: assemble the v1 summary dict (easy to unit test).

    status: "failed" if any expected table could not be counted, else "ok" when a
    champion model exists (``model`` given) and "stub" when it does not.
    """
    now = now or dt.datetime.now(dt.UTC)
    status = "ok" if model is not None else "stub"
    if any(v is None for v in row_counts.values()):
        status = "failed"   # a table we expected could not be counted
    if notes is None:
        notes = [] if model is not None else ["no champion model yet (run sd311_train)"]
    return {
        "schema_version": SCHEMA_VERSION,
        "run_date": run_date or snapshot_date,  # contract: same as the folder date
        "snapshot_date": snapshot_date,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "job_run_id": job_run_id,
        "rows_ingested": row_counts.get("bronze.open_snapshots"),
        "row_counts": row_counts,
        "data_quality_failures": data_quality_failures,
        "freshness_hours": freshness_hours,
        "model": model,
        "drift": drift,
        "notes": notes,
    }


def write_summary(summary: dict, path: str) -> None:
    """Volumes are mounted as a normal filesystem on Databricks, so plain open() works."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)


def main(argv=None):  # pragma: no cover - needs Databricks
    import mlflow
    from mlflow import MlflowClient
    from pyspark.sql import SparkSession
    from sd311_core.monitoring import drift_report, live_metrics
    from sd311_core.registry import CHAMPION, get_alias_version

    p = argparse.ArgumentParser()
    p.add_argument("--catalog", default="sd311")
    p.add_argument("--snapshot-date", default=None)
    p.add_argument("--job-run-id", default=None, help="pass {{job.run_id}} from the job")
    args = p.parse_args(argv)
    c = args.catalog
    now = dt.datetime.now(dt.UTC)
    spark = SparkSession.builder.getOrCreate()

    def one(sql):
        return spark.sql(sql).first()[0]

    def safe(fn):
        try:
            return fn()
        except Exception as e:  # missing table etc. -> not measured
            print(f"skipped: {e}")
            return None

    snapshot_date = args.snapshot_date or str(one(
        f"SELECT MAX(snapshot_date) FROM {c}.bronze.open_snapshots"))
    run_date = pacific_date(now)

    counts = {}
    for table, where in COUNTED_TABLES.items():
        sql = f"SELECT COUNT(*) FROM {c}.{table}"
        if where:
            sql += " WHERE " + where.format(d=snapshot_date)
        counts[table] = safe(lambda sql=sql: one(sql))

    newest = safe(lambda: one(
        f"SELECT MAX(CAST(date_requested AS TIMESTAMP)) FROM {c}.silver.requests_current"))
    fresh = freshness_hours(newest, now)

    d = snapshot_date
    dq = {
        "predictions_cover_features": safe(lambda: one(
            f"SELECT COUNT(*) FROM {c}.ml.features f LEFT ANTI JOIN "
            f"(SELECT * FROM {c}.ml.predictions WHERE snapshot_date = DATE '{d}') p "
            f"USING (service_request_id) WHERE f.snapshot_date = DATE '{d}'") == 0),
        "prediction_ids_not_null": safe(lambda: one(
            f"SELECT COUNT(*) FROM {c}.ml.predictions WHERE snapshot_date = DATE '{d}' "
            f"AND service_request_id IS NULL") == 0),
        "probabilities_sum_to_one": safe(lambda: one(
            f"SELECT COUNT(*) FROM {c}.ml.predictions WHERE snapshot_date = DATE '{d}' "
            f"AND ABS(p_0_2 + p_3_7 + p_8_30 + p_31_plus - 1) > 1e-3") == 0),
        "current_ids_unique": safe(lambda: one(
            f"SELECT COUNT(*) - COUNT(DISTINCT service_request_id) "
            f"FROM {c}.silver.requests_current") == 0),
    }
    notes = [f"DQ check failed: {k} ({DQ_CHECKS[k]})" for k, v in dq.items() if v is False]

    mlflow.set_registry_uri("databricks-uc")
    client = MlflowClient()
    mv = safe(lambda: get_alias_version(client, f"{c}.ml.closure_bucket", CHAMPION))
    model, drift = None, None
    if mv is not None:
        # Live metrics: predictions from the last ~60 days (28-day label window + 31-day lag).
        preds = spark.sql(
            f"SELECT * FROM {c}.ml.predictions "
            f"WHERE snapshot_date >= DATE_SUB(DATE '{d}', 62)").toPandas()
        reqs = spark.sql(
            f"SELECT r.service_request_id, r.date_requested, r.date_closed "
            f"FROM {c}.silver.requests_clean r "
            f"WHERE r.service_request_id IN (SELECT service_request_id FROM {c}.ml.predictions "
            f"WHERE snapshot_date >= DATE_SUB(DATE '{d}', 62))").toPandas()
        live = live_metrics(preds, reqs, as_of=d)
        model = {"version": str(mv.version),
                 "metrics": {"macro_f1": live["macro_f1"], "log_loss": live["log_loss"]},
                 "n_evaluated": live["n"]}
        if live["n"] == 0:
            notes.append("no predictions with final labels in the last 28 days yet "
                         "(labels lag up to 31 days)")
        ref = safe(lambda: mlflow.artifacts.load_dict(
            f"runs:/{mv.run_id}/reference_distributions.json"))
        recent = spark.sql(
            f"SELECT service_name, case_origin FROM {c}.silver.requests_first_seen "
            f"WHERE first_seen_date > DATE_SUB(DATE '{d}', 7) "
            f"AND first_seen_date <= DATE '{d}'").toPandas()
        drift = drift_report(recent, ref) if ref else None

    run_id = int(args.job_run_id) if args.job_run_id and args.job_run_id.isdigit() else None
    summary = build_run_summary(snapshot_date, counts, job_run_id=run_id, freshness_hours=fresh,
                                model=model, notes=notes or None, now=now,
                                data_quality_failures=count_dq_failures(dq), drift=drift,
                                run_date=run_date)
    path = summary_path(run_date, c)
    write_summary(summary, path)
    print(f"wrote {path}:\n{json.dumps(summary, indent=2)}")


if __name__ == "__main__":
    main()
