"""Closure-time bucket labels with an explicit as-of date.

A label must be *final*: it can never change once we assign it. With data
that stops at ``as_of`` that means:

* closed on or before as_of            -> exact bucket from days_to_close
* still open (or closed after as_of)
  and already >= 31 days old at as_of  -> "31+" (it cannot get faster)
* otherwise                            -> unlabeled (NaN); we do not know yet

``days_to_close = date_closed - date(date_requested)`` in whole days.
Slow cases are never dropped - that was the main bug in v1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BUCKETS = ["0-2", "3-7", "8-30", "31+"]
DEFAULT_AS_OF = pd.Timestamp("2025-12-31")


def days_to_bucket(days: pd.Series) -> pd.Series:
    """Map whole days to a bucket string (NaN stays NaN).

    0,1,2 -> "0-2"; 3..7 -> "3-7"; 8..30 -> "8-30"; 31+ -> "31+".
    """
    bins = [-np.inf, 2, 7, 30, np.inf]
    out = pd.cut(days, bins=bins, labels=BUCKETS, right=True)
    return out.astype("object").where(days.notna(), np.nan)


def make_labels(df: pd.DataFrame, as_of: pd.Timestamp = DEFAULT_AS_OF) -> pd.DataFrame:
    """Label every request as of ``as_of`` and say from when each label was known.

    Used everywhere a label is needed: scripts/build_dataset.py (training data),
    features.add_bucket_mix_features (mix90_*), serving.build_daily_features (daily
    job), monitoring.live_metrics and the baseline (through ``label_final_at``).

    Input
    -----
    df    : one row per request. ``date_requested`` (datetime with a time of day),
            ``date_closed`` (datetime at midnight: a date without time, or NaT if open).
    as_of : the day our data ends. Only its date counts (normalize it first), so a
            close ON the as_of day is known; a close after it is not.

    Output: a DataFrame with the SAME index as ``df`` and exactly these columns
    ----------------------------------------------------------------------------
    days_to_close  float  ``date_closed - date(date_requested)`` in whole days, if the
                          request closed on or before as_of; else NaN.
    bucket         object one of BUCKETS, or NaN when the label is not final at as_of.
    label_final_at datetime64[ns]  the first moment the label was certain, or NaT if
                          there is no label.

    Rules
    -----
    1. Closed on or before as_of        -> ``days_to_bucket(days_to_close)``.
       (Use the calendar day of date_requested: requested 23:59, closed next day = 1 day.)
    2. Not closed by as_of (open, OR closed after as_of: pretend we have not seen that
       close yet) and age at as_of >= 31 days, where age = as_of - date(date_requested)
       in whole days                    -> "31+". It can no longer be faster.
    3. Anything else (not closed by as_of and younger than 31 days) -> NaN. We don't know yet.

    label_final_at (the finality rule):
    * bucket 0-2, 3-7 or 8-30: ``date_closed + 1 day``. date_closed has no time, so we
      only know the close for sure at the END of that day (= midnight of the next day).
    * bucket "31+" (rule 2, OR a request that closed after day 31 but before as_of):
      ``date_requested + 31 days``, using the full timestamp with its time of day. That
      is the moment it turned 31 days old; from then on "31+" was certain. For a late
      close this equals min(date_closed + 1 day, date_requested + 31 days).
      (Using the close date here was a real train/serve skew bug: see lesson 08.)
    * no bucket: NaT.
    Consequence to check yourself against: once a label is final, label_final_at does not
    depend on as_of.

    Edge cases the tests check: days 2/3, 7/8 and 30/31; open at exactly 31 days ("31+")
    and 30 days (NaN); closed on as_of (labeled) vs the day after (treated as open);
    a non-default index; an as_of with a time of day.

    Hints
    -----
    1. Work with whole columns (boolean masks + ``Series.where``), no Python loop.
       Start with ``closed_by_as_of = date_closed.notna() & (date_closed <= as_of)``.
    2. ``.dt.normalize()`` drops the time of day; ``(a - b).dt.days`` gives whole days.
    3. Build label_final_at in steps: start all-NaT, fill closed rows, then overwrite
       every "31+" row with date_requested + 31 days.
    """
    as_of = pd.Timestamp(as_of).normalize()
    req_day = df["date_requested"].dt.normalize()
    closed_by_as_of = df["date_closed"].notna() & (df["date_closed"] <= as_of)

    days = (df["date_closed"] - req_day).dt.days.astype("float")
    days = days.where(closed_by_as_of)

    bucket = days_to_bucket(days)
    age_at_as_of = (as_of - req_day).dt.days
    old_and_unresolved = (~closed_by_as_of) & (age_at_as_of >= 31)
    bucket = bucket.where(~old_and_unresolved, "31+")

    # When did the label become certain?
    #  * closed in 0-30 days: only at the end of the close day (date_closed + 1).
    #  * "31+" (closed late OR still open and old): as soon as the request turned
    #    31 days old, because from then on it cannot land in a faster bucket.
    #    For a late closure that is min(date_closed + 1, date_requested + 31d),
    #    which is always date_requested + 31d. Using the close date here was a
    #    train/serve skew bug: the daily job (as_of = today) already counted these
    #    requests as "31+" at day 31, the offline dataset only from the close date.
    turned_31 = df["date_requested"] + pd.Timedelta(days=31)
    final_at = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    final_at = final_at.where(~closed_by_as_of, df["date_closed"] + pd.Timedelta(days=1))
    is_31p = bucket.eq("31+") & closed_by_as_of
    final_at = final_at.where(~is_31p, np.minimum(final_at, turned_31))
    final_at = final_at.where(~old_and_unresolved, turned_31)

    return pd.DataFrame({"days_to_close": days, "bucket": bucket, "label_final_at": final_at},
                        index=df.index)
