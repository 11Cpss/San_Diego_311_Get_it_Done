# Lesson 6: The lakehouse, Delta Lake and MERGE

## What you'll build and why it matters

You write the two statements at the heart of `databricks/tasks/snapshots.py`:

1. **`field_changes_sql()`** builds the `INSERT` that logs every tracked field whose value changed since yesterday (`silver.field_changes`).
2. **`merge_current_sql()`** builds the `MERGE` that upserts today's state into `silver.requests_current`, where a closed record always wins over a stale open one.

Why these two? They are what makes this pipeline leakage-safe and re-runnable. Upserts with `MERGE`, NULL-safe comparisons and idempotent re-runs are standard data-engineering interview topics, and here you'll be able to say "I wrote it, and here's the test that proves a stale file can't re-open a closed request." The temp view, the insert-only `first_seen` table and the DDL are given and you read them.

## Concept

**Lakehouse.** Cheap files in object storage, plus a table format that makes those files behave like database tables: transactions, schemas, UPDATE and MERGE. On Databricks that format is **Delta Lake**: Parquet files plus a `_delta_log/` folder of JSON commits. Each commit lists which files make up the table at that version. Readers only see committed versions, so a half-finished write is never visible, and you can query an old version ("time travel").

**Medallion layers** describe how much you trust and reshape the data:

| layer | in this project | rule of thumb |
|---|---|---|
| bronze | `bronze.open_snapshots`, `closed_requests` | exactly what arrived, all strings, plus where and when it came from |
| silver | `requests_first_seen`, `requests_current`, `field_changes`, `requests_clean` | one row per entity, cleaned, history kept |
| gold | `dim_*`, `fct_*`, `kpi_monthly` | shaped for questions people ask |
| ml | `ml_labels`, `features`, `predictions` | shaped for the model |

**COPY INTO and idempotency.** `COPY INTO table FROM '<folder>'` loads every file *it hasn't loaded before*; Delta records the loaded file names. Running it twice, or re-running after a crash, doesn't duplicate rows. That's **idempotency**: doing it again gives the same result. You need it because schedulers retry.

**MERGE (upsert)** handles three cases in one atomic statement:

```sql
MERGE INTO target t USING source s ON t.id = s.id
WHEN MATCHED AND <extra condition> THEN UPDATE SET ...
WHEN NOT MATCHED THEN INSERT (...) VALUES (...)
```

The extra condition on `WHEN MATCHED` is where business rules live, such as "never let an open row overwrite a closed one".

**NULL-safe comparison.** `NULL <> 'x'` is NULL, not true, so a `WHERE old <> new` filter silently drops changes *to* or *from* NULL. `old IS DISTINCT FROM new` treats NULL as a comparable value. Spark and DuckDB both support it.

**Why first-seen snapshots fix leakage.** The city republishes the *whole* open file every day with today's values. If a request is filed as "Pothole" and re-categorised two weeks later, the file only shows the final category. A model trained on it learns from a value that didn't exist when the request was filed. That's **leakage**: great offline, worse in production. `requests_first_seen` freezes each request as it looked on day one; `field_changes` records every later correction, so you can measure how often fields change.

## How it fits the pipeline

```
Lambda ─► /Volumes/sd311/bronze/landing/<source>/snapshot_date=D/*.parquet
Databricks job sd311_daily:
  1 load_bronze   COPY INTO bronze.open_snapshots / closed_requests          [given]
  2 snapshots     today_state view ─► field_changes INSERT  ─► first_seen INSERT ─► MERGE current
                                       [YOU]                  [given]            [YOU]
  3 dbt_build     requests_clean, gold, ml_labels (lesson 7)
  4 features      reads requests_first_seen (today's new ids) ─► 5 score ─► 6 evaluate_and_summarize
```

The order inside step 2 matters. The diff must run **before** the MERGE, because afterwards `requests_current` already holds today's values and there's nothing left to compare.

What breaks if your code is wrong:

- **No closed-wins guard:** a stale open row re-opens a closed request. `requests_current` shows it open again, and the DQ check and dashboards are wrong.
- **`<>` instead of `IS DISTINCT FROM`:** corrections that fill in or blank out a field are never logged, and `field_changes` under-counts exactly the leakage you're trying to measure.
- **No retry guard:** if the task crashes between the INSERT and the MERGE, the retry logs every change twice.
- **Overwriting `first_seen_date` in the MERGE:** `features.py` and `evaluate_and_summarize` lose track of when a request first appeared.

## Code walkthrough

Open these four files:

1. **`databricks/tasks/load_bronze.py`**. The bronze tables are created with an explicit all-string schema, and COPY INTO adds lineage columns while loading:
   ```sql
   COPY INTO sd311.bronze.open_snapshots
   FROM (SELECT *, _metadata.file_path AS _source_file, current_timestamp() AS _ingested_at
         FROM '/Volumes/sd311/bronze/landing')
   FILEFORMAT = PARQUET
   PATTERN = 'open/snapshot_date=*/*.parquet'
   ```
   `PATTERN` decides which sub-folders feed which table.
2. **`databricks/tasks/snapshots.py`**. Read `ddl_statements` (the three silver tables), then `today_state_sql`. It is one row per id per day, and it computes two rankings: `rn_closed_first = 1` picks the closed version when one exists (the latest truth), and `rn_open_first = 1` picks what a user could see while the request was open. Then read `first_seen_sql`, given. It is an insert-only table that uses `WHERE NOT EXISTS (...)` to skip ids it already has. Your INSERT uses the same pattern. `_cols()` and `_cols('s.')` build column lists, and `daily_statements()` returns the four statements in order.
3. **`databricks/tests/test_snapshots.py`**. Delta's jars weren't downloadable where this was built, so the test runs *the same SQL strings* in DuckDB, which supports MERGE. It also asks Spark's parser to parse every statement. The fixture is a story: request 1 is opened, corrected, then closed (and shows up in a stale open file on day 3); request 2 never changes; request 3 shows up already closed; a day 4 sends only a stale open row for request 1.
4. **`databricks/databricks.yml`**. This is where `snapshots` sits in the daily job's task chain.

## Your task

```bash
python scripts/lessons.py check 6      # fails until you're done
```

Each function returns **one SQL string**. Write SQL that both Spark and DuckDB accept: explicit column lists, no `UPDATE SET *`.

**Piece 1: `field_changes_sql(snapshot_date, tables, tracked)`**

```sql
INSERT INTO <changes> (service_request_id, field, old_value, new_value, snapshot_date)
SELECT ..., DATE '<snapshot_date>' FROM ( <one SELECT per tracked field, UNION ALL> ) diffs
WHERE NOT EXISTS (<same id, field and snapshot_date already logged>)
```

- Each per-field SELECT joins `today_state s` (only `s.rn_closed_first = 1`) to `requests_current c` on the id. It returns the field name as a literal, `c.<field>` as old and `s.<field>` as new, and keeps rows where they are `IS DISTINCT FROM` each other.
- Skip the stale case: `NOT (c.is_closed AND NOT s.is_closed)`.
- Loop over the `tracked` argument, not the constant (a test passes its own list).

**Piece 2: `merge_current_sql(snapshot_date, tables)`**

- `USING (SELECT * FROM today_state WHERE rn_closed_first = 1) s ON t.service_request_id = s.service_request_id`.
- `WHEN MATCHED AND NOT (t.is_closed AND NOT s.is_closed) THEN UPDATE SET`: every `SNAPSHOT_COLUMNS` column except the key, plus `desc_len`, `is_closed`, and `last_updated_date = DATE '<day>'`. Leave `first_seen_date` alone.
- `WHEN NOT MATCHED THEN INSERT`: all snapshot columns, `desc_len`, `is_closed`, and `first_seen_date` and `last_updated_date` both set to today.

To look at your SQL:
`python -c "import sys; sys.path.insert(0,'databricks/tasks'); import snapshots as s; print(s.merge_current_sql('2026-01-02'))"`

**Done when** `python scripts/lessons.py check 6` prints `PASS`. That's 11 tests; the Spark-parser test skips unless pyspark and Java 17 are installed. In a venv with `pip install -r databricks/requirements-dev.txt`, it parses your statements with Spark's real parser too. If you're stuck, use `python scripts/lessons.py diff 6`, or `solve 6 --yes`, which backs your version up first.

## Own it

1. Why must the diff run before the MERGE? What would `field_changes` contain if you swapped the order?
2. On day 3, request 3 has non-null fields but gets **no** change rows. Why? (What is `c.` on its first day?)
3. Walk through day 4 in `test_stale_open_row_after_close_does_not_reopen`. Which of your two guards stops which bad outcome?
4. Re-running a whole day is a no-op. Explain why for each of the four statements.

**Change one thing and predict.** Add `"comm_plan_name"` to `TRACKED_FIELDS`. Before running anything, predict whether any test fails, and how many extra rows `field_changes` would get in the 3-day story. (Hint: does any fixture row change its plan?) Then run `python -m pytest databricks/tests -q --lesson-strict -m lesson6` and print the generated SQL to see the new `UNION ALL` branch. Revert.

## Tradeoffs

- **COPY INTO vs Auto Loader.** COPY INTO is one plain SQL statement, fine for thousands of files. Auto Loader (`cloudFiles`) scales to millions and streams, but adds checkpoints and streaming concepts. We have about 3 files a day.
- **Portable SQL vs the PySpark DataFrame API.** SQL that both Spark and DuckDB accept lets us test without a cluster. The cost: no `UPDATE SET *` shortcuts, so column lists are generated from `SNAPSHOT_COLUMNS`.
- **Strings in silver snapshots.** A type change never breaks history; typing happens once, in dbt. The cost: you cast when you query silver snapshots directly.
- **Daily granularity.** Two changes within one day show up as one. Snapshots can't be finer than the source's publishing cadence.
- **Retry guard cost.** The `NOT EXISTS` on `(id, field, snapshot_date)` is an extra anti-join every day. That's fine at this size; at scale you'd partition `field_changes` by `snapshot_date`.
- **No row deletes.** A request that vanishes from the open file without appearing in a closed file stays "open" in `requests_current`. That's on purpose: no guessing. `last_updated_date` finds them.

## Interview questions

1. What does Delta Lake add on top of Parquet?
2. How does COPY INTO stay idempotent?
3. Why run the field-change diff before the MERGE?
4. What is leakage, and how do first-seen snapshots prevent it?
5. Why `IS DISTINCT FROM` instead of `<>`?
6. How do you make a MERGE-based job safe to retry after a crash halfway through?
7. How did you test Delta MERGE logic without a Databricks cluster?

## Time

| part | working | minutes |
|---|---|---|
| Read | about 1,700 words ÷ 150 wpm = 11, plus 4 files × 5 = 20 | 31 |
| Build | field_changes INSERT (medium, 30) + MERGE (medium, 30) | 60 |
| Check and explain | 2 pieces × 10 + 10 for Own it | 30 |
| **Total** | 31 + 60 + 30 = 121, rounded to the nearest 5 | **120** |

## Answers

1. **Delta on Parquet:** a transaction log (`_delta_log`) that gives ACID commits, schema enforcement and evolution, time travel, and DML (UPDATE, DELETE, MERGE) over immutable Parquet files.
2. **COPY INTO:** it records which source files it has loaded in the target table's metadata and skips them on later runs (unless `force = true`).
3. **Diff before MERGE:** the MERGE overwrites the previous state. After it, today's values are all that's left, so there's nothing to compare against.
4. **Leakage:** training on information that wasn't available at prediction time. The open file only has corrected, latest values; freezing each request as first seen means features only use what was known on day one.
5. **IS DISTINCT FROM:** `NULL <> 'x'` is NULL, so changes to or from NULL would be missed. `IS DISTINCT FROM` treats NULL as a comparable value.
6. **Retry safety:** make every step idempotent on its own. Insert-only steps use `NOT EXISTS` on their natural key (`id`, or `id + field + day`). The MERGE rewrites the same values on a re-run. A guard condition stops older data from overwriting newer state.
7. **Testing:** every statement is a plain SQL string from a small function. The tests run those exact strings in DuckDB (which has MERGE) on a multi-day story, and parse them with Spark's SQL parser to catch dialect errors.
