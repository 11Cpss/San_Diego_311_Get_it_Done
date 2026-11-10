# Lesson 7: dbt models and tests (one SQL codebase, two engines)

## What you'll build and why it matters

You write the two dbt models everything else depends on:

1. **`dbt/models/silver/requests_clean.sql`**: one cleaned row per request, the latest known state. Every gold table and the model's history window read it.
2. **`dbt/models/ml/ml_labels.sql`**: the closure-time label for every request whose outcome is *final* at an `as_of` date, plus the rule that keeps unfinished requests out.

Why these two? `requests_clean` is where cleaning rules live, and the *order* of those rules changes the numbers. `ml_labels` is the leakage guard for training labels, written in SQL. Both are what you'd walk an interviewer through when they ask "how did you build your training set?" The staging views, gold tables, macros and YAML tests are given and you read them.

## Concept

**dbt** turns a folder of `SELECT` statements into tables, in dependency order. You write `select ... from {{ ref('requests_clean') }}`. dbt works out that `requests_clean` must be built first, creates it as a table or view, and swaps in the real table name for the engine you're on.

- **Model** = one `.sql` file = one table or view. Its materialization is set per folder in `dbt_project.yml`.
- **Source** = a table dbt didn't build (our bronze tables): `{{ source('bronze', 'open_snapshots') }}`.
- **Test** = a query that returns the *bad* rows; zero rows means pass. *Generic* tests are declared in YAML (`unique`, `not_null`, `accepted_values`, `relationships`). *Singular* tests are your own `.sql` files in `tests/`.
- **`dbt build`** runs models and tests together in DAG order. If a model's test fails, everything downstream is skipped, so bad data doesn't spread.
- **Targets** (`profiles.yml`) say *where* to run: `databricks` in production, `duckdb` on your laptop and in CI. The SQL is identical.
- **Macros** are Jinja functions that write SQL. dbt's cross-database macros (`dbt.datediff`, `dbt.safe_cast`) hide dialect differences, and `adapter.dispatch` lets you add your own engine-specific versions.

**Two ideas you'll implement:**

- **Dedupe, then filter.** The same request can appear in the latest open file *and* in a closed file. Keep one row per id, closed first, and only *then* drop unwanted rows. If you drop `Referred` first, a request that was closed as Referred loses its closed row, and its stale open row wins the dedupe.
- **Label finality.** A request's bucket (0-2, 3-7, 8-30, 31+ days to close) is known at `as_of` if it closed by then, or if it has been open at least 31 days (it can only end up `31+`). A younger open request has an *unknown* label. Including it, or using a close date after `as_of`, leaks the future into training.

## How it fits the pipeline

```
bronze (Lambda + COPY INTO) ─► stg_open_snapshots / stg_closed_requests (views, typed)
   ─► requests_clean [YOU] ─┬─► dim_service, dim_location, fct_requests ─► kpi_monthly
                            ├─► ml_labels [YOU] ─► (training labels; leakage test)
                            └─► Databricks tasks: features.py (history window), train.py,
                                evaluate_and_summarize.py (live metrics)
```

In production this runs as the `dbt_build` task of the daily Databricks job, right after `snapshots` (lesson 6). Locally and in CI it runs on DuckDB over Parquet files.

What breaks if your code is wrong:

- **Dedupe wrong** (open beats closed): closed requests look open, `fct_requests` has no close dates for them, and labels move to `31+`.
- **Filters before dedupe**: Referred requests sneak back in through their stale open row.
- **Labels using a close date after `as_of`**: training sees the future. The singular test `assert_no_label_for_young_open_requests` fails, `dbt build` stops, and the daily job fails before scoring.
- **Column renamed or missing**: every downstream model fails to compile, and `features.py` breaks.

## Code walkthrough

Open these five files:

1. **`dbt/dbt_project.yml`**: per-folder schemas and materializations, the `as_of` and `local_bronze_dir` vars, and the `dispatch` search order (see file 4).
2. **`dbt/models/staging/_sources.yml`**: on Databricks the source is the real table; on DuckDB the `external_location` meta key swaps in a Parquet read. `loaded_at_field: _ingested_at` drives source freshness.
3. **`dbt/models/staging/stg_open_snapshots.sql`**: the typing layer. `dbt.safe_cast` turns garbage into NULL instead of failing the build, and `desc_len` keeps only the description's length.
4. **`dbt/macros/sd311_helpers.sql`**: `clean_service_name` (Graffiti variants become `Graffiti`, missing becomes `Unknown`), `closure_bucket`, `title_case` (dispatch: `initcap` on Databricks, a list lambda on DuckDB), `as_of_date_sql`, and `duckdb__safe_cast`.
   > **A real finding.** Writing this lesson's checker, we fed `case_age_days = 'N/A'` to the build and it crashed: dbt-duckdb's `safe_cast` is a plain `cast()`, while Databricks' is `try_cast()`. The override makes local and CI behave like production. An earlier finding: the first `dbt build` failed `not_null` on `service_name` with 4,393 rows. The fix was a rule (missing becomes `'Unknown'`), not deleting the test.
5. **`dbt/tests/assert_no_label_for_young_open_requests.sql`**: the leakage guard as a query that must return nothing.

## Your task

```bash
python scripts/lessons.py check 7      # fails until you're done (needs dbt-duckdb)
```

Both stubs return the right columns with **zero rows**, so `dbt build` and CI stay green while you work. Replace each whole file, and delete the `LESSON 7 STUB` comment block. Keep the column names and their order.

**Piece 1: `requests_clean.sql`** (do this first; `ml_labels` reads it)

1. A CTE with the latest open snapshot only (`snapshot_date = (select max(snapshot_date) ...)`).
2. `union all` it with every row of `stg_closed_requests`, tagging each row `0`/`1` as "came from the closed file".
3. `row_number() over (partition by service_request_id order by <closed first>, snapshot_date desc, _ingested_at desc)` and keep `rn = 1`.
4. *Then* filter: drop `status = 'Referred'`, `service_name = 'Other'`, and `case_age_days < 0` (NULL ages stay; `coalesce` helps).
5. Select with the normalisations: `{{ clean_service_name('service_name') }}`; `coalesce({{ title_case('comm_plan_name') }}, 'Unknown')`; `case_origin` `''`/NULL becomes `'Unknown'` (`nullif`); `council_district` NULL becomes 0; `is_closed`; `cast(date_requested as date) as date_requested_day`; `snapshot_date as source_snapshot_date`.

**Piece 2: `ml_labels.sql`**

1. `params` CTE: `select {{ as_of_date_sql() }} as as_of_date`.
2. `base`: `requests_clean` cross-joined with `params`, only `date_requested_day <= as_of_date`. Compute `days_to_close` (with `{{ dbt.datediff('r.date_requested_day', 'r.date_closed', 'day') }}`, and **only** when `date_closed <= as_of_date`) and `age_at_as_of`.
3. Final select: `label_bucket` = `{{ closure_bucket('days_to_close') }}` when closed, else `'31+'`. Add `is_censored_31_plus` and keep `days_to_close is not null or age_at_as_of >= 31`.

Fast loop while you work:

```bash
cd dbt && dbt build --target duckdb --profiles-dir . -s +ml_labels+   # model + parents + children
```

**Done when** `python scripts/lessons.py check 7` prints `PASS`. It runs 11 tests and then a full `dbt build`. The tests in `tests/integration/test_dbt_models.py` build a hand-made bronze dataset of about 25 rows; each row has a comment saying which rule it exercises, so a failure tells you which rule to look at. A final test compares row counts on the committed 1% fixtures. If you're stuck, use `python scripts/lessons.py diff 7`, or `solve 7 --yes`, which backs your version up first.

## Own it

1. Explain, with request `103` from the test data, why the filters must come after the dedupe.
2. Why is a request filed 30 days before `as_of` and still open excluded, while one filed 31 days before gets `31+`? Why is a request that closed *after* `as_of` treated as open?
3. What does `{{ ref('requests_clean') }}` give you that `silver.requests_clean` written by hand wouldn't?
4. Why does source freshness use `_ingested_at` rather than `date_requested`?

**Change one thing and predict.** Run your labels as of an earlier date:
`cd dbt && dbt build --target duckdb --profiles-dir . -s ml_labels --vars '{as_of: "2025-06-30"}'`.
Before running, predict: will the `31+` share be higher or lower than with the default `as_of` (2026-01-15), and why? Then, still in `dbt/`, check with
`python -c "import duckdb; print(duckdb.connect('sd311.duckdb').sql('select label_bucket, count(*) from ml.ml_labels group by 1'))"`.
Rebuild without `--vars` afterwards.

## Tradeoffs

- **DuckDB for CI vs testing on Databricks.** DuckDB builds the whole project in seconds with no credentials. But it isn't Databricks: `initcap`, `safe_cast` and `extract(dow)` differ. Macros and `scripts/run_spark_local.sh` (open-source Spark SQL) catch most dialect drift; a run on a real warehouse is still needed.
- **Tables vs incremental.** Everything is a full-refresh `table`: simple, correct, and cheap at about 1M rows. At 100M you'd make `fct_requests` `incremental` with a `unique_key`, and then handle late-arriving updates.
- **Tests cost warehouse time.** `unique` on a big table is a full scan. Keep tests on keys and contracts; leave statistical checks to monitoring.
- **`accepted_values` on `status`** fails the prod build if the city adds a status. That's the point: you want to notice. Use `config: {severity: warn}` to keep the pipeline running instead.
- **Labels in SQL and in Python.** `ml_labels` (SQL) and `sd311_core/labels.py` (Python) implement the same rule. The SQL table is for analysis and tests; training uses Python. Two copies can drift, so the rule is documented in both.

## Interview questions

1. What does `ref()` give you over writing table names?
2. Generic vs singular tests?
3. How do you keep one dbt project portable across engines?
4. What does source freshness check, and why on `_ingested_at`?
5. Why does `dbt build` skip downstream models when a test fails?
6. How do you build training labels without leakage?
7. Why must deduplication come before filtering in `requests_clean`?

## Time

| part | working | minutes |
|---|---|---|
| Read | about 1,600 words ÷ 150 wpm = 11, plus 5 files × 5 = 25 | 36 |
| Build | requests_clean (large, 45) + ml_labels (medium, 30) | 75 |
| Check and explain | 2 pieces × 10 + 10 for Own it | 30 |
| **Total** | 36 + 75 + 30 = 141, rounded to the nearest 5 | **140** |

## Answers

1. **`ref()`:** dependency ordering (the DAG), environment-specific names (dev vs prod schemas, DuckDB vs Unity Catalog), and lineage docs, all from the same SQL.
2. **Generic vs singular:** generic tests are reusable and parameterised in YAML (`unique`, `relationships`). Singular tests are one-off SQL files. Both pass when the query returns zero rows.
3. **Portability:** cross-database macros (`dbt.datediff`, `dbt.safe_cast`), `adapter.dispatch` for anything missing or different (`title_case`, `duckdb__safe_cast`), and no engine-only syntax in models. A second engine check (Spark SQL) runs too.
4. **Freshness:** how old the newest *loaded* row is. `_ingested_at` is when we loaded it, which shows whether the pipeline is stuck. `date_requested` would mix in the city's own reporting delays.
5. **Skip downstream:** models and tests run in DAG order, so bad data stops at the model whose test failed instead of silently rebuilding dashboards and labels on top of it.
6. **Labels without leakage:** fix an `as_of` date. Only use requests filed by then and close dates on or before it. Keep a request only if its bucket can no longer change: closed, or open at least 31 days (`31+`). Guard it with a test that fails if any young open request has a label.
7. **Dedupe first:** a request can have a stale open row and a final closed row. Filtering first could remove the closed row (for example, Referred), and then the stale open row wins the dedupe and survives, with the wrong status.
