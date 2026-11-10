# run_summary.json — schema version 1

The contract between the Databricks daily job (writer) and the AWS `monitor`
Lambda (reader). Owner: AWS side (`src/sd311/monitor.py`). Writer:
`databricks/tasks/evaluate_and_summarize.py`.

## Location

```
/Volumes/sd311/ml/run_summaries/date=YYYY-MM-DD/run_summary.json
```

`YYYY-MM-DD` is the **America/Los_Angeles** calendar date the pipeline ran,
which is also the `snapshot_date` the ingest Lambda wrote. The monitor runs at
08:00 Pacific and looks up *today's* Pacific date. (Writing the UTC date also
works today because the job runs 06:00–08:00 Pacific = 13:00–16:00 UTC, the
same calendar day; prefer `snapshot_date` to be safe.)

If the file is missing at 08:00, the monitor publishes `JobMissing=1`.

## Fields

| field | type | required | CloudWatch metric | notes |
|---|---|---|---|---|
| `schema_version` | int | yes | – | always `1` for this version |
| `run_date` | `YYYY-MM-DD` | yes | – | same as folder date |
| `snapshot_date` | `YYYY-MM-DD` | yes | – | snapshot processed |
| `generated_at` | ISO-8601 UTC | yes | – | `2026-10-08T14:03:11Z` |
| `status` | `"ok"` \| `"stub"` \| `"failed"` | yes | – | anything else is logged as a warning |
| `job_run_id` | int \| null | no | – | Databricks run id, for linking |
| `rows_ingested` | int \| null | no | `RowsIngested` | if absent, monitor uses `row_counts["bronze.open_snapshots"]` |
| `row_counts` | object `{table: int}` | no | – | free-form, for humans |
| `data_quality_failures` | int \| null | no | `DataQualityFailures` | count of failed DQ checks/expectations |
| `freshness_hours` | float \| null | no | `FreshnessHours` | hours between now and newest `date_requested` |
| `model` | object \| null | no | – | null until model evaluation exists |
| `model.version` | string | – | – | registered model version |
| `model.metrics.macro_f1` | float \| null | – | `LiveMacroF1` | on yesterday's predictions vs realised labels |
| `model.metrics.log_loss` | float \| null | – | `LogLoss` | |
| `drift` | object \| null | no | – | |
| `drift.max_psi` | float \| null | – | `MaxPSI` | max PSI over monitored features |
| `drift.psi_by_feature` | object `{feature: float}` | – | – | for humans |
| `notes` | list[string] | no | – | |

**Null means "not measured"**: the monitor skips null/absent values rather than
publishing 0, so a missing F1 never looks like a terrible model to the alarm.
`JobMissing=0` is published whenever the file exists.

## Example

```json
{
  "schema_version": 1,
  "run_date": "2026-10-08",
  "snapshot_date": "2026-10-08",
  "generated_at": "2026-10-08T14:10:00Z",
  "status": "ok",
  "job_run_id": 777,
  "rows_ingested": 1234,
  "row_counts": {"bronze.open_snapshots": 1234},
  "data_quality_failures": 0,
  "freshness_hours": 9.5,
  "model": {"version": "3", "metrics": {"macro_f1": 0.61, "log_loss": 1.2}},
  "drift": {"max_psi": 0.05, "psi_by_feature": {"service_name": 0.05}},
  "notes": []
}
```

The Databricks stub (`status: "stub"`, `model: null`, only `row_counts`) is
valid v1 and yields `JobMissing=0` and `RowsIngested`.

## Changing the schema

Additive optional fields: keep version 1. Renaming/removing/retyping a field:
bump to 2, update `SUPPORTED_SCHEMA_VERSIONS` in `monitor.py` first, deploy AWS,
then switch the writer.
