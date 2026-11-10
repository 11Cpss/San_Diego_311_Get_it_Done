# Lesson 4: The ingest Lambda (data contract and the Databricks hand-off)

## What you'll build and why it matters

You write two pieces of the ingest Lambda. The rest of it is given, and you read it.

1. **The data contract**: `build_schema()` in `src/sd311/schemas.py`. It's a pandera schema that decides whether a city CSV is good enough to load. Every row the model ever sees has passed it.
2. **The hand-off to Databricks**: `DatabricksClient.upload_file()` and `DatabricksClient.run_now()` in `src/sd311/ingest.py`. These two HTTP calls are the only connection between the AWS half and the Databricks half. One puts the day's Parquet file where Databricks reads it, and the other starts the daily job.

Why these two? They answer the two questions interviewers ask about any ingestion pipeline: "what happens when the source sends bad data?" and "how do your systems talk to each other?" The S3 key layout, the manifest and the metrics are plumbing. Read them; you don't need to retype them.

## Concept

Every morning a small program wakes up, downloads the city's CSV files, checks they look right, stores them, and tells Databricks to process them. That program is an **AWS Lambda**: code that runs only when triggered. It's billed by the millisecond, and there's no server to manage.

- **Why a Docker image?** pandas and pyarrow are too big for a zip Lambda (250 MB unzipped). A container image can be up to 10 GB, and you can run the exact same image on your laptop.
- **Why S3 first?** S3 is the *source of truth*. We store the untouched CSV plus a `manifest.json` "receipt" (sha256, row count, HTTP headers). If anything downstream breaks, we can rebuild from raw.
- **Data contract.** The schema is the minimum a file must satisfy to be usable: the required columns exist, ids are present, and dates parse. A file that breaks the contract goes to `quarantine/`, a metric fires, and the run stops, so garbage never reaches the model. The contract is deliberately *minimal*: everything is read as a string, and real typing happens later in dbt. A contract that's too strict blocks good data every time the city changes something harmless.
- **Idempotent keys.** `raw/<source>/snapshot_date=YYYY-MM-DD/<file>` means re-running a day overwrites the same key. S3 versioning keeps the old copy.
- **Freshness.** If today's sha256 equals yesterday's, the city didn't refresh the file. The `StaleSource` metric is 1, and an alarm fires after two days.
- **The hand-off.** Databricks exposes REST APIs. The **Files API** writes bytes into a Unity Catalog *Volume* (a governed folder): `PUT /api/2.0/fs/files/Volumes/...`. The **Jobs API** starts a job: `POST /api/2.1/jobs/run-now` with `{"job_id": 42}`, which returns a `run_id`. Both authenticate with a bearer token that the Lambda reads from SSM Parameter Store at runtime.

## How it fits the pipeline

```
06:00 PT  EventBridge Scheduler ─► handler(event)                       [given]
            for each file: process_source()                            [given]
              download ─► read_csv_as_strings ─► validate() ─► build_schema()   [YOU]
                 ├─ problems ─► quarantine/ + DataQualityFailures metric ─► STOP
                 └─ ok ─► raw/ + manifest.json ─► StaleSource metric
                          ─► Parquet ─► dbx.upload_file(/Volumes/sd311/bronze/landing/...)  [YOU]
            all files landed ─► dbx.run_now(job_id from SSM)            [YOU]
                 ─► Databricks job sd311_daily: load_bronze (COPY INTO) ─► snapshots ─► dbt ─► score
08:00 PT  monitor Lambda reads run_summary.json ─► alarms
```

What breaks downstream if your code is wrong:

- **Schema too strict** (for example, it rejects blank `date_closed`, which every open request has): every day is quarantined. The `DataQualityFailures` alarm fires, no data lands, and at 08:00 `JobMissing` fires too.
- **Schema too loose** (it doesn't check `date_requested`): unparseable dates reach bronze, `safe_cast` turns them into NULL in dbt, and those requests silently drop out of the labels.
- **Wrong upload path or method**: `load_bronze`'s `COPY INTO ... PATTERN 'open/snapshot_date=*/*.parquet'` finds no new file. Bronze stops growing, dbt source freshness warns after 36 hours, and the run summary shows 0 rows ingested.
- **run_now wrong**: the Databricks job never starts, and the monitor raises `JobMissing` at 08:00.

The same client is also used by `scripts/day_one_check.py` (your first test against a real workspace) and `scripts/backfill.py`.

## Code walkthrough

Open these four files:

1. **`src/sd311/ingest.py`**, top to bottom. Section 1 holds the pure helpers (keys, manifest, `is_stale`) with no network, so they're trivially testable:
   ```python
   def is_stale(manifest):
       prev = manifest.get("previous_sha256")
       return prev is not None and prev == manifest["sha256"]
   ```
   Section 2 holds the network clients. `http_session` adds retries with exponential backoff on 429 and 5xx responses, and a browser-like User-Agent (the city's server rejects some non-browser clients). Read `download_file` and `get_run`: they are your templates for `upload_file` and `run_now`. Section 3 is the orchestration. Notice that `process_source` wraps **only the CSV parse** in `try/except`. A file that won't parse is bad data, but an exception inside `validate()` is a bug in our code, and it must crash loudly instead of being filed as "bad data".
2. **`src/sd311/schemas.py`**. Two helpers are given: `_parses_as_datetime` (element-wise `pd.to_datetime(..., errors="coerce")`) and `_is_str`. `validate()` runs your schema with `lazy=True`, which collects *every* failure instead of stopping at the first one, and turns pandera's failure table into readable messages. `get_schema()` builds the schema once, on first use.
3. **`tests/integration/test_ingest_end_to_end.py`**. The whole Lambda runs with fake AWS (moto) and fake HTTP (`responses`). This works because of dependency injection: `handler(event, context, s3=..., ssm=..., cw=..., session=...)`. In Lambda the arguments default to real boto3 clients; tests pass fakes. No monkeypatching is needed.
4. **`docker/Dockerfile`**. Dependencies go in their own layer first, so it's cached until `requirements.txt` changes; the code layer comes last because it changes often. One image serves two functions: Terraform sets `image_config.command` to `sd311.ingest.handler` or `sd311.monitor.handler`.

## Your task

Start by running the checker to see what's missing:

```bash
python scripts/lessons.py check 4      # fails until you're done
```

**Piece 1: `build_schema()`** in `src/sd311/schemas.py`. Return a `pa.DataFrameSchema` (`import pandera.pandas as pa` is already there):

| column(s) | rule |
|---|---|
| `service_request_id` | required, not null, no empty strings (`pa.Check.str_length(min_value=1)`) |
| `date_requested` | required, not null, every value parses (`pa.Check(_parses_as_datetime, error="...")`) |
| `status` | required, not null |
| the rest of `REQUIRED_COLUMNS` | required; blank or null values are fine |
| `OPTIONAL_COLUMNS` | checked only if present (`required=False`), nullable |
| any other column | allowed (the city adds columns): `strict=False` |

All columns hold strings, so the dtype is `str`, with `coerce=False`. Hint: build a `dict` of `pa.Column` objects. Write the three special columns first, then loop over the two lists, and don't overwrite the special ones (`dict.setdefault`). Check just this piece with:

```bash
python -m pytest tests/unit/test_schema.py --lesson-strict -q
```

**Piece 2: `upload_file()` and `run_now()`** in `src/sd311/ingest.py`. The docstrings give the exact contract:

- `upload_file(path, data, overwrite=True)`: `PUT {host}/api/2.0/fs/files{path}`, query `overwrite="true"/"false"`, headers `self.auth` plus `Content-Type: application/octet-stream`, and the raw bytes as the body. Raise on non-2xx.
- `run_now(job_id, job_parameters=None)`: `POST {host}/api/2.1/jobs/run-now` with the JSON `{"job_id": int(job_id)}`. Add `"job_parameters"` only if parameters were given. Raise on non-2xx and return `int(resp.json()["run_id"])`.

Always use `self.session` (it carries the retries) and `timeout=self.timeout`. The fake server in `tests/unit/test_databricks_client.py` only answers requests that match the API exactly.

**Done when** `python scripts/lessons.py check 4` prints `PASS`. That's 23 tests: the schema, the client, the end-to-end Lambda run and the day-one script. If you're stuck for more than 15 minutes on one piece, `python scripts/lessons.py diff 4` shows the answer key next to your code. `python scripts/lessons.py solve 4 --yes` puts the key in place and backs your version up to `.lesson_backup/`. Use them to unblock yourself, then rewrite the piece in your own words.

## Own it

Answer these out loud, or in a note, without looking at the code:

1. On the day the city adds a column `priority`, what happens with your schema? What would happen with `strict=True`? Which alarm would fire, and what would the on-call person see in S3?
2. Trace `test_bad_schema_is_quarantined_and_stops`. Which S3 keys are written, which metric is sent, and why does the closed file never even get downloaded?
3. Why does `run_now` convert `job_id` to `int`, and why does it send no `job_parameters` by default?
4. `process_source` used to wrap both parsing and `validate()` in one `try/except Exception`. What bug would that hide, and what would the on-call person wrongly conclude?

**Change one thing and predict.** In your `build_schema`, make `council_district` non-nullable (`nullable=False`). Before running anything, write down which tests in `tests/unit/test_schema.py` you expect to fail. About 5% of the rows in the 1,000-row fixture have a blank district. Then run them. Explain the result. (Hint: look at `read_csv_as_strings`. What does a blank cell become with `keep_default_na=False`?) Revert the change afterwards.

## Tradeoffs

| Choice | Upside | Downside |
|---|---|---|
| Lambda vs. a cron VM | $0 when idle, no patching | 15-minute and 10 GB limits; cold starts |
| Image vs. zip | big dependencies are fine; same image locally | slower first deploy; needs ECR |
| Minimal string contract | never fails on harmless changes | typing is pushed to the silver layer (dbt) |
| Stop on any bad file | no partial days downstream | one bad closed file blocks the open file |
| `requests` + REST vs. the Databricks SDK | small image; you see every HTTP call | you maintain the client (about 40 lines) |
| Byte-level freshness (sha256) | one comparison, no parsing | a re-export with a new timestamp column looks "fresh" |

## Interview questions

1. Why store the raw file before transforming it?
2. How do you detect a source that stopped updating?
3. What makes this Lambda idempotent?
4. Why not raise an exception when a file fails the schema?
5. How did you test cloud code without a cloud account?
6. How does your AWS side hand data to Databricks, and how is that call authenticated?
7. What goes in your data contract, and what deliberately stays out?

## Time

| part | working | minutes |
|---|---|---|
| Read | about 1,700 words ÷ 150 wpm = 11, plus 4 files × 5 = 20 | 31 |
| Build | schema (medium, 30) + hand-off, two methods (medium, 30) | 60 |
| Check and explain | 2 pieces × 10 + 10 for Own it | 30 |
| **Total** | 31 + 60 + 30 = 121, rounded to the nearest 5 | **120** |

## Answers

1. **Raw first:** replays and audits. You can rebuild everything from immutable input, while transformations can be wrong and get fixed later.
2. **Stale source:** hash every snapshot and compare it to the previous one; alarm when the hashes are identical N days in a row (here, 2).
3. **Idempotent:** deterministic, date-partitioned keys (a re-run overwrites the same objects and Volume files), and `COPY INTO` downstream loads each file only once.
4. **No raise on bad data:** it's an expected data problem, not a code bug. Quarantine plus a `DataQualityFailures` metric gives a clear alert and keeps the evidence. Raising would also trigger Lambda's automatic async retries, which would re-download the file for nothing.
5. **Testing without a cloud:** moto fakes S3, SSM and CloudWatch in memory; `responses` fakes HTTP; the clients are injected into `handler`, so tests pass fakes without monkeypatching.
6. **Hand-off:** the Lambda converts each CSV to Parquet and `PUT`s it into a Unity Catalog Volume with the Files API, then calls Jobs `run-now` once all files have landed. Both calls use a bearer token stored as an SSM SecureString, read at runtime. It never appears in Terraform, environment variables or code.
7. **Contract:** in: the required columns exist, ids are non-empty, and `date_requested` parses. Out: types, value ranges and category lists. Those change harmlessly and are handled (and tested) in dbt, where a bad value becomes NULL instead of blocking the whole day.
