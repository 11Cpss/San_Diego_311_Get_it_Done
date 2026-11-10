# Lesson 2: Labels and "as-of" features

> Code paths are relative to the repo root. The model code lives in `ml/`; ADRs are in `docs/adr/`.

## What you'll build and why it matters

You will write the two functions every row of training data depends on: `make_labels`, which
decides each request's bucket *and the moment that bucket became known*, and `_backlog`, the
"how many requests were already waiting" feature. Both run in training and in the daily
Databricks job, so after this lesson the labels the model learns from, and the backlog it sees
every morning, come from your code. Interviewers ask about exactly this: point-in-time
correctness and leakage.

## Concept

Two questions sit behind every row of training data:

1. **When did we know the label?** A request closed on 2025-03-02 has a known label from the end
   of that day. A request that is still open after 31 days is known to be `31+` from day 31, even
   though it's still open. A 10-day-old open request has **no label yet**. Guessing one would be
   inventing data.
2. **What did we know when the request arrived?** Every feature must be computable at
   `t = date_requested` using only things that happened **before** t. This is an **as-of**
   (point-in-time) feature. If a feature would change when you add rows from the future, it leaks.

The test for both is the same: *could I have computed this at that moment, with the data that existed then?*

Two details make it subtle here:
- `date_closed` is a **date without a time**. "Closed on 2025-03-02" might mean 08:00 or 23:59.
  So a close is only *certain* at the end of that day, and a request closed on the same calendar
  day as t must still count as open at t.
- **`31+` becomes certain before the request closes**: at day 31 it can no longer land in a
  faster bucket. Getting this moment wrong caused a real train/serve skew bug (lesson 08).

## How it fits the pipeline

```
make_labels ──► build_dataset.py ──► dataset.parquet ──► backtest / fit_final        (training, laptop)
     │      └─► label_final_at ──► add_bucket_mix_features (mix90_*) and fit_baseline (lesson 3)
     ├────────► serving.build_daily_features ──► databricks/tasks/features.py        (daily job)
     └────────► monitoring.live_metrics ──► databricks/tasks/evaluate_and_summarize.py (daily)
_backlog ─► add_backlog_features ─► build_features ─► training AND the daily features task
```

- `ml/scripts/build_dataset.py` calls `make_labels(df, 2025-12-31)` once for 2.56 M rows, then
  `build_features`, which calls `add_backlog_features` → `_backlog` twice (by service, by district).
- The daily Databricks `features` task calls `serving.build_daily_features`, which runs the
  *same* `build_features` (and so your two functions) on a bounded history window with
  `as_of = today`.
- `live_metrics` re-labels with `make_labels(as_of=today)` to grade old predictions.
- dbt's `ml_labels` model (lesson 07) is the SQL twin of the bucket rule.

## Code walkthrough

**1. `ml/sd311_core/labels.py`** (open it). `BUCKETS` and `days_to_bucket` are given:

```python
bins = [-np.inf, 2, 7, 30, np.inf]
out = pd.cut(days, bins=bins, labels=BUCKETS, right=True)    # 2 -> "0-2", 3 -> "3-7", 31 -> "31+"
```

Below it is the docstring of `make_labels`, your first function. Read the module docstring at
the top too: "a label must be final; it can never change once we assign it".

**2. `ml/sd311_core/features.py`** (open it). Read the module docstring (the searchsorted trick
and the tie rule), then the given helpers you will call:

```python
def count_before(event_key, event_min, query_key, query_min, inclusive=False):
    combined = np.sort(event_key * _K + event_min)          # events grouped by key, sorted by time
    upto  = np.searchsorted(combined, query_key * _K + query_min, side="left")
    start = np.searchsorted(combined, query_key * _K, side="left")
    return upto - start                                      # same key AND time < query time
```

`add_workload_features` is a 6-line model of how to use it (`count_in_window` = two
`count_before` calls). `add_bucket_mix_features` shows how `label_final_at` is consumed:
only labels with `label_final_at` in `(t-90d, t]` count, so an early `label_final_at` leaks and a
late one under-counts. `_backlog` is your second function; `add_backlog_features` calls it.

**3. `ml/sd311_core/serving.py`** (open it, read `build_daily_features` only). Note
`build_features(cleaned, as_of=snap)`: the daily path is the training code with a different
`as_of`. That is why a bug in `label_final_at` became train/serve skew.

**4. `ml/tests/test_labels.py` and `ml/tests/test_features_leakage.py`** (open both). These are
your checker. Read the test names: they are the edge cases. The leakage tests are *invariance*
tests: add future rows, or move a close date further into the future, and assert the features at
t do not change.

Speed: cleaning, labels and all features for 2.56 M rows take about 15 seconds, with no Python
loop over rows. Your code must keep it that way.

## Your task

### 1. `make_labels(df, as_of)` in `ml/sd311_core/labels.py`

- **Returns** a frame with the same index and columns `days_to_close`, `bucket`, `label_final_at`.
- **Bucket rules:** closed on or before `as_of` → `days_to_bucket(days)`, counting from the
  *calendar day* of the request; not closed by `as_of` (open, or closed after it) and at least
  31 days old → `"31+"`; otherwise NaN.
- **Finality rule:** `date_closed + 1 day` for 0-2/3-7/8-30; `date_requested + 31 days` (keep the
  time of day) for every `"31+"`, including one that closed late; NaT when there is no label.
- **Edge cases tested:** days 2/3, 7/8, 30/31; open at exactly 30 vs 31 days; closed on `as_of`
  vs the day after; `as_of` with a time of day; a non-default index.
- **Hints:** docstring hints 1–3. Hint 3 (build `label_final_at` in steps) saves the most time.

### 2. `_backlog(df, key)` in `ml/sd311_core/features.py`

- **Returns** an int64 array: for each row, same-key requests submitted strictly before t and not
  closed before the calendar day of t.
- **Tie rules:** same minute is not "before"; closed on the same day as t still counts as open;
  rows need not be sorted.
- **Hints:** docstring hints 1–3. The whole function is two `count_before` calls and a subtraction;
  the work is choosing the right events and query times. A double loop is fine as a first
  draft, but the version you keep must be vectorised.

### Check

```bash
python scripts/lessons.py check 2
```

This runs 49 tests marked `lesson2`: your hand-computed label and backlog cases, the leakage tests,
and the serving and live-metrics tests that call your functions (the daily path must give the
same features as training). Then it runs `python3 ml/scripts/verify_real_data.py labels` (about
1 minute): it cleans all 2.56 M real requests, runs **your** `make_labels` and backlog, and
compares every value with `ml/data/dataset.parquet`. `PASS ... reproduce data/dataset.parquet
exactly` means the training data is now produced by your code. (No data on your machine yet?
It prints `SKIPPED`; set `SD311_DATA_DIR` and run `python3 ml/scripts/build_dataset.py` once.)

If stuck: `python scripts/lessons.py diff 2`, or `solve 2 --yes` (your version is backed up).

## Own it

1. Explain the tie rule for `_backlog` in two sentences, and say which direction it can be wrong in
   and why that direction is the safe one.
2. Why is `label_final_at` for a request that closed on day 45 equal to `date_requested + 31 days`
   and not `date_closed + 1 day`? What would go wrong in the daily job if it were the close date?
3. Walk through the backlog subtraction on the `mixed` case in `test_features_leakage.py` by hand,
   row by row.
4. **Change one thing and predict.** In your `make_labels`, make late-closed `"31+"` rows final at
   `date_closed + 1 day` again (the old bug). Before running, write down which tests you expect
   to fail and whether `verify_real_data.py labels` will report differences in `bucket`,
   `label_final_at` or the backlog columns. Run `python scripts/lessons.py check 2`, compare,
   then undo the change.

## Tradeoffs

- **Conservative tie rule vs. accuracy:** treating same-day closures as open slightly inflates
  backlog. The alternative (counting them as closed) could leak closures that happened after t.
- **Finality as early as is safe, never earlier:** a `31+` label is final at day 31, a faster one
  only at the end of its close day. Too late under-uses known outcomes and, worse, makes the
  offline features disagree with the daily job; too early leaks.
- **Excluding unlabeled rows** means the last 31 days before any cutoff can't be trained on. That's
  why the backtest has a 31-day embargo (lesson 3).
- **Snapshot data:** our raw files are one snapshot from January 2026. If the city edited a
  request's service or district after submission, we see the edited value. True as-of features
  need daily snapshots, which the new pipeline provides (lesson 06).
- **Vectorised vs. readable:** the searchsorted version is less obvious than a loop but runs on
  2.5 M rows in about a second; the hand-computed tests are what keep it honest.

## Interview questions

1. What is a point-in-time (as-of) feature?
2. How would you unit-test for leakage?
3. Why do you need an embargo between training and test periods here?
4. Your dates have no time of day. How do you handle "closed before t"?
5. Why count open and closed requests in the workload feature, rather than only closed ones?
6. When is a label "final", and why does that timestamp matter for features?

## Time

| part | working | minutes |
|---|---|---|
| Read | 1,580 words ÷ 150 wpm ≈ 11, + 5 files opened (labels.py, features.py, serving.py, 2 test files) × 5 = 25 | 36 |
| Build | `make_labels`: large (about 15 lines of mask logic, as_of/NaT edge cases and the finality rule) 45; `_backlog`: small (two `count_before` calls, but the tie rule) 15 | 60 |
| Check and explain | 2 functions × 10 + "Own it" 10 | 30 |
| **Total** | 36 + 60 + 30 = 126 | **125** |

## Answers

1. A feature computed using only data that existed at the prediction moment: a join or window
   filtered on `event_time < t`, plus a rule for timestamps that tie or are coarse.
2. Invariance tests: add rows from after t, or change outcomes that happen after t, and assert
   features at t don't change. Pair them with small hand-built cases with known answers.
3. Labels take up to 31 days to become final. Without the gap, the last training rows would carry
   labels that weren't known at the cutoff, so you'd train on future information.
4. Choose the conservative side: count it as closed only if it closed on an earlier day, and
   document the rule.
5. At time t you don't know which requests will close. Counting only rows that later closed uses
   the future, and it undercounts busy periods, which are exactly when service is slow.
6. When it can no longer change: end of the close day, or day 31 for `31+`. Features like the
   90-day bucket mix may only use labels final by t; using the wrong moment leaks (too early) or
   makes training and serving disagree (too late, our real bug).
