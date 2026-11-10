# STATUS: Databricks half (databricks/, dbt/, lessons 06–07)

> Historical checkpoint from October 8, 2026. For the current AWS deployment,
> see [STATUS_AWS.md](STATUS_AWS.md). Databricks remains unconnected.

Written 2026-10-08, about 14:00 PDT. Nothing was deployed or pushed, because there were no credentials.

## Done (and actually run)

| item | evidence |
|---|---|
| dbt project, DuckDB target | `cd dbt && dbt build --target duckdb --profiles-dir .` gives **PASS=46 WARN=0 ERROR=0** (10 models, 36 tests) on the committed 1% fixture *and* on the full 2024–2025 + open data |
| CI compatibility | A fresh checkout copy (no local_data) ran `dbt deps; dbt parse; dbt build --target duckdb` with `DBT_PROFILES_DIR=.` as in `.github/workflows/ci.yml`: green |
| dbt on Spark SQL dialect | `bash dbt/scripts/run_spark_local.sh` (open-source Spark 4.0.1 via dbt-spark session, 5% sample) gives **PASS=46** |
| source freshness | `dbt source freshness --target duckdb` passes for both bronze sources |
| `as_of` var | `--vars '{as_of: "2025-06-30"}'` builds green; labels capped at that date |
| snapshot MERGE logic | `pytest databricks/tests -q` gives **25 passed**. The same SQL strings run in DuckDB 1.5.6 (MERGE) on a day-1/2/3(+4) story: a new request, field changes, closing, never-seen-open, a stale open row after close, an idempotent re-run, and a crash-retry. Every statement is also parsed by Spark 4.0.1's SQL parser. A mutation check confirmed the tests fail when the closed-wins guard or the retry guard is removed. |
| bundle YAML | `test_bundle_yaml.py`: task chain, serverless envs, and the files exist. Both jobs load cleanly into the typed `databricks-bundles` 1.20 models, which reject misspelled fields. |
| load_bronze | SQL builders are unit-tested. The CREATE TABLE statements are parsed by Spark. |
| run_summary.json | Matches `docs/run_summary_schema.md` v1 (required fields, `rows_ingested`, `freshness_hours`, `job_run_id`, nulls for unmeasured values) and is unit-tested |
| docs | `databricks/README.md`, `dbt/README.md`, `lessons/06_lakehouse_and_delta_merge.md`, `lessons/07_dbt_models_and_tests.md`. The lesson 07 exercise solution was built once to check it's doable, then removed. |

Full-data dbt row counts: requests_clean 825,838 · fct_requests 825,838 · dim_service 40 ·
dim_location 118 · dim_date 4,383 · fct_daily_backlog 326 · kpi_monthly 13,220 ·
ml_labels 812,399 (0-2 339,488 / 3-7 138,963 / 8-30 129,095 / 31+ 204,853).

## Not done / not verified

- **Nothing has run on a real Databricks workspace.** These parts are unverified:
  - `COPY INTO ... FROM (SELECT *, _metadata.file_path ...) PATTERN ... mergeSchema`. Only its string form is tested, because COPY INTO is Databricks-only SQL.
  - `snapshot_date=` folders may also be inferred as a partition column. The values match the in-file column, but check this on the first run.
  - The dbt task without `profiles_directory` (connection taken from warehouse, catalog and schema). If it fails, set `profiles_directory: ../dbt` and add `--target databricks`.
  - `databricks bundle validate`. The CLI and the GitHub/Maven downloads were blocked, so it couldn't be run.
  - Delta-specific execution of the MERGE. The Delta jars couldn't be fetched from Maven, so the MERGE ran on DuckDB, not Delta.
- **ML tasks are stubs** (`features`, `score`, `train`), and `evaluate_and_summarize` is a partial stub (`model`, `drift`, `data_quality_failures` are null). Each one's docstring names the intended `sd311_core` calls.
- `dbt compile --target databricks` needs a live connection. Only `dbt parse --target databricks` was run, and it succeeded.

## How to verify

```bash
cd /home/claude/sd311-pipeline
# Python tests (pyspark needs a venv on this box; system Python skips the 4 Spark/bundles checks)
/home/claude/.venv-spark/bin/python -m pytest databricks/tests -q          # 25 passed
python3 -m pytest databricks/tests -q                                       # 21 passed, 4 skipped
# dbt on DuckDB
cd dbt && dbt build --target duckdb --profiles-dir .                        # PASS=46
python scripts/make_local_data.py && dbt build --target duckdb --profiles-dir . --vars '{local_bronze_dir: local_data/bronze}'
# dbt on local Spark SQL
PATH=/home/claude/.venv-spark/bin:$PATH bash scripts/run_spark_local.sh    # PASS=46
# On a workspace (later)
cd ../databricks && databricks bundle validate -t dev --var="warehouse_id=<id>" --var="notification_email=<you>"
```

## Open questions / notes for the other agents

1. **AWS deploy workflow** runs `databricks bundle deploy -t prod` with no `--var`. `warehouse_id` and `notification_email` have no defaults, so the prod environment needs `BUNDLE_VAR_warehouse_id` and `BUNDLE_VAR_notification_email` set as env vars. Otherwise the deploy fails.
2. **Run-summary date.** The folder date is the `snapshot_date` (max in `bronze.open_snapshots`), as the schema doc recommends. `freshness_hours` uses the newest `date_requested` in `requests_current`, against the session clock (UTC on serverless). The city's timestamps look local, so expect a constant offset of about 7–8 h. Should we convert to UTC?
3. **Backfill days.** `snapshots` processes one `snapshot_date` (the latest by default). If a backfill lands in an older folder, run `snapshots.py --snapshot-date <that day>` once.
4. **Model repo interface.** Stubs reference `sd311_core.features.build_features(df, as_of, labels)`, `train.fit_model`, `train.predict` and `metrics.evaluate` as they existed at 13:40. Expected wheel path: `/Volumes/sd311/ml/wheels/sd311_core-0.1.0-py3-none-any.whl` (`var.sd311_core_wheel`).
5. **Cleaning choices to confirm.** About 0.5% of rows have a null `service_name`; these become `'Unknown'` (needed so every fact joins `dim_service`). Dedupe runs *before* the Referred filter. `accepted_values` on status (`New`, `In Process`, `Closed`) will fail prod if the city adds a status. That's intended, but we could switch it to `severity: warn`.
6. **Free Edition.** `CREATE CATALOG` may be denied. If so, use `--skip-create-catalog` or point `var.catalog` at the default catalog.

## Files

`databricks/`: `databricks.yml`, `README.md`, `requirements-dev.txt`, `.gitignore`,
`tasks/{load_bronze,snapshots,features,score,evaluate_and_summarize,train}.py`,
`tests/{test_snapshots,test_load_bronze,test_bundle_yaml,test_evaluate_and_summarize}.py`

`dbt/`: `dbt_project.yml`, `profiles.yml` (duckdb, databricks, spark_local), `README.md`, `.gitignore`,
`macros/{generate_schema_name,sd311_helpers}.sql`, `models/staging/*` (sources and 2 views),
`models/silver/requests_clean.sql`, `models/gold/*` (6 tables), `models/ml/ml_labels.sql`, schema YAMLs,
`tests/{assert_closed_not_before_requested,assert_no_label_for_young_open_requests}.sql`,
`fixtures/bronze/*.parquet` (1% sample, 544 KB),
`scripts/{make_local_data.py,spark_local_setup.py,run_spark_local.sh}`

`lessons/06_lakehouse_and_delta_merge.md`, `lessons/07_dbt_models_and_tests.md`

