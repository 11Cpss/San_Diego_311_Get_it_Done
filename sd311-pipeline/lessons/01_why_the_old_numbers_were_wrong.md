# Lesson 1: Why the old numbers were wrong

> Code paths are relative to the repo root. The model code lives in `ml/`; ADRs are in `docs/adr/`.

## What you'll build and why it matters

You will write `truncation_evidence`, the function that measures how much v1's test set hid:
how many recent requests it silently dropped, and how the share of slow (`31+`) requests
changes once they are put back. These two numbers are why the old resume headline
(MAE 9.71 → 8.00) is retired, and they are your strongest interview story. After this lesson
the evidence in `ml/reports/old_vs_new.md` is computed by your code.

## Concept

A test set is supposed to look like the future you will face. v1's test set did not.

v1 built its data from the **closed** files only. A request submitted in November 2025 that is
still open in January 2026 is not in any closed file, so it was not in the test set. That
means the test set lost exactly the slow requests. A test set made only of requests that
finished quickly makes any "predict short" model look good.

This is called **right-censoring** (we stop watching before some outcomes happen) combined with
**selection on the outcome** (we kept rows *because* their outcome was small). The
`case_age_days <= 730` filter is a second, smaller form of the same mistake: it uses the answer to
decide which questions are on the exam.

How to spot it: **a smarter baseline did worse than a dumber one.** A per-service median should
beat a single global median, because sidewalk repairs and parking complaints really do take
different amounts of time. In v1 it was the other way round (25.9 vs 9.71 MAE). The per-service
medians came from training data, where slow sidewalk jobs had finished. In the truncated test set,
only the sidewalk jobs that happened to finish fast were left, so the honest per-service guess
looked terrible. When a sensible baseline loses badly, look at the data before you look at the model.

## How it fits the pipeline

This is offline analysis, not part of the daily job. `ml/analysis/old_vs_new.py` runs on your
laptop once, after `ml/scripts/build_dataset.py` has written `ml/data/dataset.parquet`. Its
output (`ml/reports/old_vs_new.json` and `.md`) is quoted in `ml/reports/RESULTS.md`, in
`lessons/00_START_HERE.md` and in the resume bullet. The fixes it motivates *are* in the
pipeline: open requests are kept by cleaning, labeled by `make_labels` (you write it in lesson 2)
and scored only once final by the backtest folds (lesson 3).

```
raw CSVs ─► v1_dataset()  (closed files only, case_age <= 730)  ─► v1 test set ─┐
raw CSVs ─► build_dataset.py ─► dataset.parquet (closed + open, as-of labels) ──┴─► truncation_evidence() ─► reports/
```

## Code walkthrough

**1. `ml/analysis/old_vs_new.py`** (open it). Read it top to bottom; only your function is empty.

```python
def v1_dataset(raw_dir=None):
    c = load_closed(raw_dir or DEFAULT_RAW_DIR, years=range(2023, 2026))   # closed files only  <- censoring
    ...
    c = c[c["case_age_days"] <= 730]                                       # outcome-based filter
```

- `H2_START`, `H2_END`, `Q3_END` are the three dates your function uses. All windows are half-open.
- `compute()` rebuilds v1, reproduces its two baselines (the 9.71 and 25.93 MAE), loads the
  proper population from `dataset.parquet`, calls `truncation_evidence(full, test)` and adds the
  "January gap" estimate.
- `verify()` recomputes everything and compares it with the committed `reports/old_vs_new.json`.

**2. `ml/reports/old_vs_new.md`** (open it). These are the numbers your function must reproduce:

| | value |
|---|---|
| H2-2025 requests in the cleaned data | 180,360 |
| ... missing from v1's test set | 22,940 (12.7%), all still open in the snapshot |

| bucket (Q3-2025 submissions) | v1 truncated test | proper labels |
|---|---|---|
| 0-2 | 50.9% | 46.7% |
| 3-7 | 18.3% | 16.8% |
| 8-30 | 16.4% | 15.0% |
| 31+ | 14.4% | **21.4%** |

About 38% of the true `31+` cases from that quarter (roughly 7,800 of 20,400) were invisible to v1.

**3. `ml/sd311_core/cleaning.py`** (open it). `clean_requests` cleans **closed + open rows
together**, keeps the closed record when an id appears in both (`dedupe_prefer_closed`) and has
no 730-day filter. Also glance at `days_to_bucket` at the top of `ml/sd311_core/labels.py`: you
call it, it is given.

The other v1 problems, for context:
- **Leakage:** `service_name_detail` may be filled in after submission
  (`docs/adr/0003-exclude-service-name-detail.md`). After matching the service mix the
  missingness evidence is weak; we exclude it as a precaution.
- **Workload features** counted only closed, cleaned rows, so "how busy was this team" used
  outcomes. `add_workload_features` now counts every request submitted in the window.
- **Selection on test:** v1 ranked trials by test MAE. v2 uses only the validation month for choices.

## Your task

**Function:** `truncation_evidence(proper, v1_test)` in `ml/analysis/old_vs_new.py`.

**Contract (full version in the docstring):**
- `proper` has `service_request_id`, `date_requested`, `source` ("closed"/"open"), `bucket`.
  `v1_test` has `service_request_id`, `date_requested`, `case_age_days`.
- Return a dict with 8 keys: H2 count, H2 missing count and share, missing-and-still-open count,
  Q3 row counts on both sides, and the Q3 bucket shares on both sides (v1's from
  `days_to_bucket(case_age_days)`).

**Rules to get right:** half-open windows (a request at exactly `Q3_END` is Q4); "missing" means
the id is not in `v1_test`; shares are among rows that have a bucket (NaN left out); every share
dict has all four buckets in order, 0.0 when absent, rounded to 4 decimals; an empty window gives
0 and 0.0, never NaN or an error.

**Hints:** the three numbered hints at the end of the docstring. Read hint 2 before you write the
shares.

**Check:**

```bash
python scripts/lessons.py check 1
```

That runs the 8 hand-computed tests in `ml/tests/test_old_vs_new.py`, then
`python3 ml/analysis/old_vs_new.py --verify`, which reruns the whole analysis on the real data
with your function and compares every number with `reports/old_vs_new.json`. Seeing
`PASS: your truncation_evidence reproduces every number` means the 12.7% and 21.4% in your
resume story are produced by your code. (If `ml/data/dataset.parquet` or the raw CSVs are not on
your machine yet, that step prints `SKIPPED`; build the dataset after lesson 2 and rerun. Set
`SD311_DATA_DIR` to the folder with the slim CSVs.)

If stuck: `python scripts/lessons.py diff 1` after you have tried, or `solve 1 --yes` to see the
answer key (your version is backed up in `.lesson_backup/`).

## Own it

1. Explain out loud, without notes, why the *per-service* median baseline lost to the *global*
   median in v1. Use the words "censoring" and "selection on the outcome".
2. Your function returns `h2_2025_missing_still_open == h2_2025_missing_from_v1_test`
   (22,940 both). What would it mean if some missing requests were *closed*?
3. Why does the bucket comparison use Q3 only, not the whole second half of 2025?
4. **Change one thing and predict.** In `v1_dataset`, delete the `case_age_days <= 730` line.
   Before running anything, write down which numbers in `old_vs_new.json` you expect to change
   and which must stay the same, and why. Then run `python3 ml/analysis/old_vs_new.py --verify`
   (it prints `DIFF` next to each changed number) and compare with your prediction. Put the line back.

## Tradeoffs

- Keeping open cases forces a coarser target (buckets) because we don't know their final
  duration. We trade precision ("about 12 days") for honesty ("more than a month").
- Ending evaluation at 2025-09-30 throws away Q4-2025 test data so that every test label is final.
- There is still a small hole: requests closed between 2026-01-01 and the extract date
  (2026-01-14) are in neither file. One year earlier the same window held 0.23% of Jan-Sep
  requests, so the effect is small but not zero.

## Interview questions

1. Your model beats the baseline on a holdout set, but a more sensible baseline does much worse than
   a naive one. What do you suspect?
2. What is right-censoring, and how did it show up here?
3. Why is filtering `case_age_days <= 730` a problem even if it removes only a few rows?
4. How would you check whether a field is available at prediction time?
5. Why can't you rank 20 model variants by test score and report the best one?

## Time

| part | working | minutes |
|---|---|---|
| Read | 1,390 words ÷ 150 wpm ≈ 9, + 3 files opened in the walkthrough × 5 = 15 | 24 |
| Build | `truncation_evidence`: medium (about 15 lines, half-open windows, NaN buckets, empty windows) | 30 |
| Check and explain | 1 function × 10 + "Own it" 10 | 20 |
| **Total** | 24 + 30 + 20 = 74 | **75** |

## Answers

1. Something is wrong with the evaluation data. A common cause is a holdout selected on the
   outcome, for example only completed cases. Check how the holdout was built before touching the model.
2. We stop observing before some events happen. Requests still open at extract time have no close
   date. Dropping them removes the slowest cases, mostly from the most recent period, which is
   exactly the test period. Here: 22,940 of 180,360 H2-2025 requests (12.7%).
3. It uses the target to choose rows. At prediction time you can't know a case will exceed 730
   days, so the filter creates a population that won't exist in production.
4. Compare it on records seen right after creation with completed records (here, newest open vs.
   closed), after controlling for mix. Better: snapshot records over time and see whether the field
   changes. Treat the result as evidence, not proof.
5. The test set then becomes part of model selection, and the best score is biased upward
   (winner's curse). Choose on validation and report test once.
