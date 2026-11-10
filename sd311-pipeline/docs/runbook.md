# Runbook — sd311 alarms

Every alarm emails the SNS topic `sd311-alerts`. Each section: **what it
means → how to check → how to fix**. Region is `us-west-2`.

Handy commands:

```bash
# Last ingest run's log lines (JSON, one per step)
aws logs tail /aws/lambda/sd311-ingest --since 1d --format short
# Logs Insights query for errors
#   fields @timestamp, step, source, error, problems | filter level = "ERROR"
# Re-run ingest manually for a date (no Databricks trigger)
aws lambda invoke --function-name sd311-ingest \
  --payload '{"snapshot_date":"2026-10-08","trigger_job":false}' \
  --cli-binary-format raw-in-base64-out out.json && cat out.json
```

---

## ingest-errors

**Alarm:** `sd311-ingest-errors` — AWS/Lambda `Errors` > 0 for `sd311-ingest`.

**Means:** the ingest function crashed: city server down/blocked, timeout,
out of memory, missing SSM parameter, or Databricks API refused the upload.
(A *bad file* does not crash: that is `data-quality-failures`.)

**Check:**
1. `aws logs tail /aws/lambda/sd311-ingest --since 1d` → find the `"level": "ERROR"` line; its `step` says where it failed.
2. `step=download` → open the URL in a browser. 403 = UA blocked; 5xx = city outage.
3. `Task timed out` / `Runtime.OutOfMemory` → check the `REPORT` line's Max Memory Used.
4. `missing SSM parameters` → `aws ssm get-parameters-by-path --path /sd311/databricks`.
5. `databricks_upload` 401/403 → token expired or lacks WRITE VOLUME.

**Fix:** city outage → wait and re-run manually. Memory/timeout → raise
`memory_size`/`timeout` in `infra/main.tf`. Token → rotate it:
`aws ssm put-parameter --name /sd311/databricks/token --type SecureString --value ... --overwrite`.
Then re-invoke (command above, `trigger_job` true).

## data-quality-failures

**Alarm:** `sd311-data-quality-failures` — `SD311/DataQualityFailures` > 0.

**Means:** a city file failed the pandera schema (ingest wrote it to
`quarantine/` and stopped, so Databricks did *not* run today), **or** the
Databricks summary reported failed DQ checks.

**Check:**
1. `aws s3 ls s3://<bucket>/quarantine/ --recursive | tail`
2. `aws s3 cp s3://<bucket>/quarantine/<source>/snapshot_date=<d>/<file>.errors.json -` lists the problems (missing column, unparseable dates, null ids).
3. If nothing is quarantined, open today's `run_summary.json` (Databricks UI → Catalog → sd311.ml.run_summaries).

**Fix:** if the city renamed/removed a column, decide whether it is really
required: update `REQUIRED_COLUMNS` in `src/sd311/schemas.py`, add a test, ship
via PR. If the file was just truncated/garbled, re-run tomorrow or manually
once the city fixes it. Never edit raw files by hand.

## stale-source

**Alarm:** `sd311-stale-source-2d` — `SD311/StaleSource{Source=open}` = 1 on 2 consecutive days.

**Means:** the open-requests file was byte-identical (same sha256) to the
previous snapshot two days running. The city's export probably stopped.

**Check:** compare `sha256` and `http_headers.Last-Modified` in
`raw/open/snapshot_date=<d>/manifest.json` for the last 3 days. Check
data.sandiego.gov for an outage notice.

**Fix:** nothing to fix on our side. If it lasts > 1 week, email the city's
open-data team and add a note to the README. Model metrics will degrade
because no new cases arrive; expect `LiveMacroF1` noise.

## job-missing

**Alarm:** `sd311-databricks-job-missing` — `SD311/JobMissing` ≥ 1, or **no data** (treated as breaching).

**Means:** at 08:00 there was no `run_summary.json` for today. Either the
Databricks job failed/was slow, ingest never triggered it, or the monitor
Lambda itself did not run (no data at all).

**Check:**
1. Ingest logs: is there a `databricks_run_now` line with a `run_id`? If ingest stopped earlier, see the alarms above.
2. Databricks UI → Workflows → `sd311_daily` → that run id → which task failed.
3. Monitor logs: `aws logs tail /aws/lambda/sd311-monitor --since 1d`.

**Fix:** repair/re-run the Databricks job, then re-invoke the monitor:
`aws lambda invoke --function-name sd311-monitor --payload '{}' --cli-binary-format raw-in-base64-out out.json`.
If the job is just slow (serverless cold start), consider moving the monitor
schedule later in `infra/main.tf`.

## live-macro-f1-low

**Alarm:** `sd311-live-macro-f1-low` — `SD311/LiveMacroF1` < `var.f1_alarm_threshold` (default 0.45).

**Means:** yesterday's predictions scored worse than the threshold on
realised labels. Could be real model decay, a label/feature bug, or a tiny
evaluation sample.

**Check:** in `run_summary.json`, look at `row_counts` (sample size) and
`drift`. If `MaxPSI` is also high → input shift. If counts are tiny → noise.

**Fix:** small sample → ignore or raise the minimum sample in the
evaluation task. Real decay → retrain (Databricks owns this), compare against
the current model, promote only if better. Bug → fix and backfill.

## max-psi-high

**Alarm:** `sd311-max-psi-high` — `SD311/MaxPSI` > 0.2.

**Means:** some input feature's distribution today differs a lot from
training (PSI > 0.2 is the usual "significant shift" rule of thumb).

**Check:** `drift.psi_by_feature` in the summary names the feature. Common
causes: a new `service_name` category, a council-district re-map, a city
system change, a storm or holiday spike.

**Fix:** if it's a one-day event, acknowledge and wait. If it persists or
F1 drops too, retrain on recent data. If a category was renamed, add a
mapping in dbt/silver and retrain.
