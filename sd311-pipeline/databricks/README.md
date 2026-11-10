# databricks/: the lakehouse half of the SD 311 pipeline

The AWS side lands the city's Get It Done files as Parquet in a Unity Catalog
volume. This folder holds the Databricks code that turns those files into bronze,
silver, gold and ML tables every morning. It's packaged as a **Databricks Asset Bundle**
(`databricks.yml`) and runs entirely on **serverless** compute.

```
AWS Lambda ──Parquet──► /Volumes/sd311/bronze/landing/<source>/snapshot_date=YYYY-MM-DD/*.parquet
                                   │
   sd311_daily job (06:00 PT)      ▼
   1 load_bronze       COPY INTO ─► sd311.bronze.open_snapshots / closed_requests
   2 snapshots         MERGE     ─► sd311.silver.requests_first_seen / requests_current / field_changes
   3 dbt_build         dbt       ─► sd311.silver.requests_clean, sd311.gold.*, sd311.ml.ml_labels
   4 features          pandas    ─► sd311.ml.features
   5 score             MLflow    ─► sd311.ml.predictions
   6 evaluate_and_summarize      ─► /Volumes/sd311/ml/run_summaries/date=YYYY-MM-DD/run_summary.json
                                          │
                                          ▼  read by the AWS monitor Lambda (docs/run_summary_schema.md)
```

## Catalog layout (Unity Catalog)

| object | what's in it | written by |
|---|---|---|
| `sd311.bronze.landing` (volume) | raw Parquet from AWS | AWS ingest Lambda |
| `sd311.bronze.open_snapshots` | every open-file row, every day, all strings, plus `_source_file` and `_ingested_at` | `load_bronze` |
| `sd311.bronze.closed_requests` | rows from the `closed_<YEAR>` and `backfill` files | `load_bronze` |
| `sd311.silver.requests_first_seen` | each request as it looked the first day we saw it (insert-only), plus `desc_len` = length of `public_description` (0 if missing) | `snapshots` |
| `sd311.silver.requests_current` | latest known state per request; closed wins over open (MERGE) | `snapshots` |
| `sd311.silver.field_changes` | `id, field, old_value, new_value, snapshot_date` (append-only) | `snapshots` |
| `sd311.silver.requests_clean` + staging views | typed, cleaned, deduped requests | dbt |
| `sd311.gold.*` | `dim_service`, `dim_location`, `dim_date`, `fct_requests`, `fct_daily_backlog`, `kpi_monthly` | dbt |
| `sd311.ml.ml_labels` | closure-bucket labels that are final at `as_of` | dbt |
| `sd311.ml.run_summaries` (volume) | `date=YYYY-MM-DD/run_summary.json` | `evaluate_and_summarize` |

## The tasks

| file | status | what it does |
|---|---|---|
| `tasks/load_bronze.py` | real | Creates the catalog, schemas and volumes if they're missing, then creates the two bronze Delta tables (explicit all-string schema) and runs `COPY INTO` for each one. COPY INTO skips files it has already loaded, so re-runs are safe. |
| `tasks/snapshots.py` | real | Builds a `today_state` view, appends `field_changes`, inserts new ids into `requests_first_seen`, then MERGEs into `requests_current`. Plain SQL strings, tested in DuckDB. |
| dbt (`../dbt`) | real | See `../dbt/README.md`. |
| `tasks/features.py` | implemented (glue) | Spark reads a bounded window, `sd311_core.serving.build_daily_features` builds features for requests first seen today → `ml.features`. |
| `tasks/score.py` | implemented (glue) | Loads `models:/<catalog>.ml.closure_bucket@champion`, `sd311_core.serving.score_frame` → `ml.predictions` (today's partition replaced). Skips if no champion yet. |
| `tasks/evaluate_and_summarize.py` | implemented | Full v1 `run_summary.json`: row counts, Pacific-time freshness, DQ checks, live macro-F1/log-loss (28-day final-label window), PSI drift vs the champion's training reference. |
| `tasks/train.py` | implemented (glue) | Backtest + MLflow logging + register + champion/challenger alias (`sd311_core.registry`). |

All task logic lives in `ml/sd311_core/{serving,monitoring,registry}.py` and is unit-tested in
`ml/tests/`. The task files only read/write tables. None of it has run on a real workspace yet
(see `STATUS_INTEGRATION.md`).

## Testing locally (no Databricks needed)

```bash
python -m venv .venv && . .venv/bin/activate        # pyspark won't build with Debian's system setuptools
pip install -r databricks/requirements-dev.txt       # needs Java 17+ for the pyspark parse checks
pytest databricks/tests -q                           # 25 tests
```

- `test_snapshots.py` runs the exact `snapshots.py` SQL in DuckDB on a 3-day story: a new request, field changes, a request that closes, a request never seen open, a stale open row after the close, an idempotent re-run, and a retry after a crash mid-day. It also parses every statement with Spark's own SQL parser.
- `test_load_bronze.py` checks the generated DDL and COPY INTO SQL. COPY INTO is Databricks-only, so it can't execute locally.
- `test_bundle_yaml.py` checks the task chain and the serverless environments, and loads each job into the typed `databricks-bundles` models. Those models reject misspelled fields.
- `test_evaluate_and_summarize.py` checks the summary against `docs/run_summary_schema.md`.

**Why not run Spark with Delta locally?** The Delta jars come from Maven, and Maven was blocked in the build sandbox. If you can reach Maven, `configure_spark_with_delta_pip` from `delta-spark==4.0.0` with `pyspark==4.0.1` should let you run `snapshots.daily_statements()` with `spark.sql` unchanged.

## Deploy (when you have a workspace)

```bash
# one-time: install the CLI (https://docs.databricks.com/dev-tools/cli/install) and log in
databricks auth login --host https://<your-workspace>.cloud.databricks.com

cd databricks
databricks bundle validate -t dev --var="warehouse_id=<id>" --var="notification_email=<you>"
databricks bundle deploy   -t dev --var="warehouse_id=<id>" --var="notification_email=<you>"
databricks bundle run      -t dev sd311_daily
databricks bundle run      -t dev sd311_train      # manual job
```

- **CI deploy** (`.github/workflows/deploy.yml` runs `databricks bundle deploy -t prod`): variables without
  defaults must come from env vars `BUNDLE_VAR_warehouse_id` and `BUNDLE_VAR_notification_email`
  (or `--var`). Otherwise the deploy fails with a missing-variable error.
- In `dev` (development mode), job names get a `[dev <you>]` prefix and schedules are paused. Run jobs by hand.
- In `prod` (production mode), the daily schedule (06:00 America/Los_Angeles) is live.
- `warehouse_id` is under SQL Warehouses → your warehouse → Connection details. The dbt task needs it.
- The `ml` environment installs the repo wheel (`sd311` + `sd311_core`). `bundle deploy` builds it via the `artifacts:` block (`pip wheel` at the repo root → `dist/*.whl`) and uploads it; the dependency is listed as `../dist/*.whl`. This follows the bundle docs for wheels in job tasks; the serverless-environment form has not been checked with `bundle validate`.

## Free Edition caveats

- **Serverless only.** Free Edition has no classic clusters, and that's why every task uses an `environment_key` instead of `new_cluster`.
- **One small SQL warehouse.** The dbt task and any dashboards share it. `dbt build` over about 1M rows should be fine, but expect queueing.
- **Creating catalogs.** If `CREATE CATALOG` is denied in your workspace, create `sd311` once in the UI, or run with `--skip-create-catalog` (add it to the `load_bronze` parameters). You could also point `var.catalog` at the workspace's default catalog.
- **Quotas.** Free Edition limits compute per day and can stop jobs that run too long. Keep `max_concurrent_runs: 1`.
- **Outbound network.** Serverless egress can be restricted. Installing `dbt-databricks` from PyPI usually works. If it doesn't, put a wheel in a volume and list it as a dependency.
- **No external S3 locations** on Free Edition. That's why the AWS side writes into a UC *volume* and doesn't register an external location.

## Not verified on a real workspace

These were written from the docs and checked offline, but they haven't run on Databricks yet:

- `COPY INTO ... FROM (SELECT *, _metadata.file_path ...)` with `PATTERN` and `mergeSchema`.
- Hive-style `snapshot_date=` folders: Spark may also infer them as a partition column. The value matches the in-file column.
- The dbt task without `profiles_directory`. The connection should come from the warehouse, catalog and schema settings. If it doesn't, set `profiles_directory: ../dbt` and use the `databricks` target in `profiles.yml`.
- `databricks bundle validate`. Instead, the YAML was checked against the typed `databricks-bundles` 1.20 models.
