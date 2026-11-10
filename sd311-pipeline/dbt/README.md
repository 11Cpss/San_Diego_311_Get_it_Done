# dbt/: SQL models for silver, gold and ML labels

The same SQL runs on three engines:

| target | engine | used for |
|---|---|---|
| `databricks` | Databricks SQL warehouse (Unity Catalog, Delta) | production (the `dbt_build` task in `../databricks/databricks.yml`) |
| `duckdb` (default) | a local DuckDB file | learning and CI. Fast, with no Java and no credentials. |
| `spark_local` | open-source Spark in-process (`dbt-spark[session]`) | a local check of the Spark/Databricks SQL dialect |

Portability comes from dbt's **cross-database macros**: `dbt.safe_cast`,
`dbt.datediff`, `dbt.date_trunc`, `dbt.date_spine`, `dbt.hash` and `dbt.concat`. A
small `adapter.dispatch` macro covers `title_case`, because DuckDB has no `initcap`.
One more override, `duckdb__safe_cast`, turns dbt-duckdb's plain `cast()` into
`try_cast()`, so a malformed value (say `case_age_days = 'N/A'`) becomes NULL locally
too, as it does on Databricks, instead of failing the whole build. `dbt_project.yml`
lists this project first in the `dispatch` search order so dbt picks the override.

## Model DAG

```
sources (bronze, loaded by databricks/tasks/load_bronze.py)
  bronze.open_snapshots ──► stg_open_snapshots ─┬─► requests_clean ─┬─► dim_service ──┐
  bronze.closed_requests ─► stg_closed_requests ┘        (silver)   ├─► dim_location ─┤
                                    │                                │   dim_date ─────┤
                                    │                                ├─► fct_requests ◄┘ (relationships tests to all 3 dims)
                                    │                                │        └──────► kpi_monthly
                                    │                                └─► ml_labels  (ml)
                                    └──────────────────────────────────► fct_daily_backlog
```

| layer | model | grain / notes |
|---|---|---|
| staging (views) | `stg_open_snapshots` | request × snapshot_date, typed with `safe_cast` (bad values become NULL) |
| | `stg_closed_requests` | one row per row in the closed files (may contain duplicates) |
| silver | `requests_clean` | one row per request. Closed beats open. Drops `Referred`, `Other` and negative ages. Any graffiti variant becomes `Graffiti`, and a missing service becomes `Unknown`. `comm_plan_name` is title-cased (missing → `Unknown`), missing `case_origin` → `Unknown`, missing district → `0`. |
| gold | `dim_service` | service_name, `is_infrastructure` (the 9 listed services) |
| | `dim_location` | (comm plan, district) with a hashed `location_id` and `is_district_5_6` |
| | `dim_date` | one row per day, 2016–2027 (`dbt.date_spine`) |
| | `fct_requests` | one row per request: keys, `days_to_close`, `closure_bucket` |
| | `fct_daily_backlog` | snapshot day × service × district: open count and median age. History grows one day per run. |
| | `kpi_monthly` | month × district × service: volume, share in each bucket and still open, median days to close |
| ml | `ml_labels` | requests whose bucket is final at `as_of` (var; defaults to the latest snapshot) |

Tests: unique/not_null on ids, `accepted_values` (status and bucket labels),
`relationships` from `fct_requests` to all three dims and from `ml_labels` to
`requests_clean`, source freshness on `_ingested_at`, and two singular tests in
`tests/`: no request closes before it was requested, and no label exists for a
request that's still open and younger than 31 days at `as_of`.

## Run locally (DuckDB)

```bash
pip install dbt-duckdb                       # tested: dbt-core 1.12.5, dbt-duckdb 1.11.0, duckdb 1.5.6
cd dbt
# 1) Quick: the committed 1% fixture (fixtures/bronze, about 9k rows). This is what CI runs.
dbt build --target duckdb --profiles-dir .   # 10 models + 36 tests, writes dbt/sd311.duckdb

# 2) Full 2024-2025 data, built from /home/claude/data/raw/*.slim.csv.gz
python scripts/make_local_data.py            # writes local_data/bronze/*.parquet (git-ignored)
dbt build --target duckdb --profiles-dir . --vars '{local_bronze_dir: local_data/bronze}'

dbt source freshness --target duckdb --profiles-dir .
dbt build --target duckdb --profiles-dir . --vars '{as_of: "2025-06-30"}'   # labels as of another date
```

On DuckDB, `meta.external_location` in `models/staging/_sources.yml` makes the
bronze sources read the local Parquet files directly. dbt-databricks ignores that
key and reads the real `sd311.bronze.*` tables.

Last local runs (2026-10-08): `PASS=46 WARN=0 ERROR=0` on both the fixture and the full data. Full-data row counts:

| relation | rows |
|---|---|
| silver.requests_clean | 825,838 |
| gold.dim_service | 40 |
| gold.dim_location | 118 |
| gold.dim_date | 4,383 |
| gold.fct_requests | 825,838 |
| gold.fct_daily_backlog | 326 |
| gold.kpi_monthly | 13,220 |
| ml.ml_labels | 812,399 (0-2: 339,488 · 3-7: 138,963 · 8-30: 129,095 · 31+: 204,853) |

With the 1% fixture: requests_clean 8,284, ml_labels 8,151, kpi_monthly 3,008, fct_daily_backlog 165.

Spark dialect check (needs Java 17+ and `pip install pyspark "dbt-spark[session]"`):

```bash
bash dbt/scripts/run_spark_local.sh          # 5% sample, plain Parquet tables; PASS=46 on 2026-10-08
```

## Run on Databricks

- **As part of the job.** The `dbt_build` task runs `dbt source freshness` and then `dbt build` on the SQL warehouse `var.warehouse_id`, with catalog `sd311`. Custom schemas map to `silver`, `gold` and `ml` (see `macros/generate_schema_name.sql`).
- **From your laptop:**
  ```bash
  export DBT_DATABRICKS_HOST=<workspace>.cloud.databricks.com
  export DBT_DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/<id>
  export DBT_DATABRICKS_TOKEN=<personal access token>
  pip install dbt-databricks
  cd dbt && dbt build --target databricks --profiles-dir .
  ```
  This hasn't been run against a real workspace yet. `dbt parse --target databricks` succeeds offline.

## Design choices worth knowing

- **Dedupe first, then filter.** If a request is open in the snapshot but closed as `Referred`, it's dropped. It doesn't fall back to its stale open row.
- **`fct_daily_backlog` uses the snapshots, not the closed dates.** Each day's open file *is* the backlog as of that day, so the table doesn't have to rebuild history from `date_closed`, which can be revised. The catch: history only starts when the pipeline starts.
- **`ml_labels` honours `as_of`.** A request that closed *after* `as_of` counts as still open at `as_of`. That way a training set "as of June" never uses July's information.
