# Start here

This repo is your San Diego 311 project rebuilt as a daily pipeline. The plumbing is written for you. The core pieces are empty, and you write them across nine lessons. When you're done, the code that labels requests, builds features, validates data, moves it between AWS and Databricks, cleans it in the warehouse and decides which model goes live is **your code**, and you can explain why it works.

You never write test cases. The tests are already written and act as your checker.

## How a lesson works

1. **Read** the lesson file. Each one has: what you'll build and why it matters, the concept, how it fits the pipeline, and a walkthrough of the code around your piece.
2. **Build** the stubbed function(s) or file(s) named in "Your task". Each stub has a docstring with the rules, the edge cases and a few hints. Until you finish it, it raises `NotImplementedError("[lesson N] ...")` (or carries a `LESSON N STUB` marker in SQL/Terraform/YAML).
3. **Check** with `python scripts/lessons.py check N`. It runs that lesson's tests strictly; some lessons also rerun the real data through your code.
4. **Own it:** answer the explain-back prompts out loud and try the "change one thing and predict" experiment. Then do the interview questions; the answers are at the bottom of the lesson.

```bash
pip install -e ".[ml,dev]"                    # once
export SD311_DATA_DIR=/path/to/slim/raw/files # real-data checks (see ml/README.md)
python scripts/lessons.py status              # where you are in every lesson
python scripts/lessons.py check 2             # your checker for lesson 2
python scripts/lessons.py diff 2              # after you pass: compare with the answer key
python scripts/lessons.py solve 2             # stuck or short on time: put the answer key in place
python scripts/lessons.py reset 2             # start the lesson over (your version is backed up)
```

A normal `pytest` run skips tests that hit an unfinished stub instead of failing, so the repo stays green while you work.

## The lessons

Times use one rubric for every lesson:

- **Read:** lesson words at 150 words/min, plus 5 min per file you open.
- **Build:** 15 / 30 / 45 min per piece (small / medium / large).
- **Check and explain:** 10 min per piece (max 25), plus 10 min for "Own it".

| # | Lesson | You write | Time |
|---|---|---|---|
| 1 | Why the old numbers were wrong | `truncation_evidence()`: measures how much the old test set dropped (`ml/analysis/old_vs_new.py`) | 75 min |
| 2 | Labels and as-of-time features | `make_labels()`: the 4 buckets and when each label becomes final; `_backlog()`: open requests at submission time (`ml/sd311_core/labels.py`, `features.py`) | 125 min |
| 3 | Baselines, metrics, backtesting | `make_fold()`: train/valid/test windows with the 31-day embargo; `fit_baseline()`: per-service mix (`ml/sd311_core/backtest.py`, `baseline.py`) | 110 min |
| 4 | Ingest: data contract + Databricks hand-off | `build_schema()`: the pandera contract; `upload_file()` and `run_now()`: the AWS → Databricks calls (`src/sd311/schemas.py`, `ingest.py`) | 120 min |
| 5 | Terraform: least-privilege IAM + schedule | The ingest Lambda's IAM policy and its 06:00 Pacific schedule (`infra/main.tf`) | 100 min |
| 6 | Lakehouse: field changes and Delta MERGE | `field_changes_sql()` and `merge_current_sql()` (`databricks/tasks/snapshots.py`) | 120 min |
| 7 | dbt: cleaning and label models | `requests_clean.sql` (your cleaning rules in SQL) and `ml_labels.sql` | 140 min |
| 8 | MLflow, daily scoring, monitoring | `psi()`: drift; `promotion_decision()`: champion vs challenger (`ml/sd311_core/monitoring.py`) | 120 min |
| 9 | CI/CD | `.github/workflows/ci.yml` | 70 min |
| | **Total** | 16 pieces of core code | **about 16 h 20 min** |

**Order:** do them in number order; each lesson builds on ideas from the one before.

- **Lesson 1:** its real-data check needs the dataset that lesson 2's labels build. Do lesson 1's function and reading first, and rerun its check after lesson 2.
- **Lesson 3:** includes an optional 8-minute step where your code reproduces one backtest fold from `ml/reports/RESULTS.md` exactly.
- **Lesson 9:** CI only checks real things after you write it.

**Pace:** about 2 hours a day finishes everything in 8–9 days. Lessons 1–3 come first because they are the story you'll tell in interviews.

**Deploying before you finish:** `solve` the lessons you haven't done yet, deploy, then come back to them. When your version passes `check`, it replaces the answer key.

## Where things stand (Oct 8, 2026)

| Part | State |
|---|---|
| Model v2 (`ml/`) | Built and backtested on real data. The saved model and `RESULTS.md` came from the answer key; your lesson 2–3 code reproduces them exactly |
| AWS code (`src/sd311`, `infra/`, `docker/`) | Lambdas tested with fake AWS. Terraform and Docker are written but not yet validated |
| Databricks (`databricks/`) | Tasks written; MERGE logic tested locally. Never run on a real workspace |
| dbt (`dbt/`) | 47 models and tests pass on DuckDB, stubs included |
| Tests | With every answer key in place: 199 passed. With stubs: 44 passed, 159 waiting on you |
| Deployed | Nothing yet |

## The honest numbers

- **Backtest:** 5 quarterly folds, 456,265 test requests.
- **Macro-F1:** 0.531 for the model vs 0.432 for the per-service baseline.
- **Log-loss:** 0.917 vs 1.065.
- **Every fold:** the model wins on all three metrics.
- **Where it helps:** the 3–7, 8–30 and 31+ buckets.
- **Where it doesn't:** for 0–2 days, the baseline is as good or better.
- **What it's for:** rough expectations, not exact promises.

The old headline (MAE 9.71 → 8.00) came from a test set missing 12.7% of requests, the slowest ones. Don't use it. Lesson 1 shows why.

Status files with full details: `STATUS_AWS.md`, `STATUS_DATABRICKS.md`, `STATUS_INTEGRATION.md`, `ml/reports/RESULTS.md`.
