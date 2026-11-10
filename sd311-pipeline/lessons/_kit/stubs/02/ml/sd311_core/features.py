"""Submission-time features.

Rule for every feature: it may only use information that existed at the
moment ``t = date_requested`` of the request being scored.

The time-window counts (workload, backlog, bucket mix) are done with
*sorted arrays + np.searchsorted* instead of a Python loop per row. The
trick, in plain words:

1. Turn each event into one integer:  ``key_code * K + minutes``.
   Because K is bigger than any ``minutes`` value, all events of one key
   sit together in a sorted array, ordered by time inside the block.
2. "How many events with key k happened before time t?" is then
   ``searchsorted(sorted, k*K + t) - searchsorted(sorted, k*K)``.
3. A window [t-30d, t) is just  before(t) - before(t-30d).

That is O(n log n) for all 3 million rows at once.

Tie rule for backlog: ``date_closed`` is a date without a time. We only
treat a request as closed at time t if ``date_closed < date(t)``, i.e. it
closed on an EARLIER calendar day. Something that closed later on the same
day as t still counts as open (backlog). This can only over-count the
backlog a little; it can never use a closure that happened after t.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from .labels import BUCKETS, DEFAULT_AS_OF, make_labels

_EPOCH = pd.Timestamp("2000-01-01")
_K = np.int64(2 ** 26)  # > number of minutes between 2000 and 2100

CATEGORICAL_FEATURES = [
    "service_name", "case_origin", "case_record_type", "comm_plan_name", "council_district",
    "service_x_comm",
]
NUMERIC_FEATURES = [
    "hour", "day_of_week", "month", "is_holiday", "desc_len", "has_description", "is_followup",
    "workload_30d_service", "workload_30d_service_comm",
    "backlog_service", "backlog_district",
    "mix90_0_2", "mix90_3_7", "mix90_8_30", "mix90_31p", "mix90_n",
]
# Excluded on purpose (see docs/adr and analysis/field_availability.py):
#   service_name_detail - filled in after submission (leakage)
#   year                - a trend proxy that does not extrapolate
# case_record_type and is_followup were checked the same way and showed no
# sign of being filled in later, so they are kept (reports/field_availability.md).
EXCLUDED_FEATURES = ["service_name_detail", "year"]
FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES


def to_minutes(ts: pd.Series) -> np.ndarray:
    """Timestamps -> int64 minutes since 2000-01-01 (NaT -> -1)."""
    m = (ts - _EPOCH) // pd.Timedelta(minutes=1)
    return m.fillna(-1).astype("int64").to_numpy()


def count_before(event_key, event_min, query_key, query_min, inclusive=False):
    """For each query, count events with the same key and time < query time.

    With ``inclusive=True`` the condition is time <= query time.
    Keys must be non-negative integer codes shared by events and queries.
    """
    combined = np.sort(event_key.astype("int64") * _K + event_min.astype("int64"))
    block_start = query_key.astype("int64") * _K
    side = "right" if inclusive else "left"
    upto = np.searchsorted(combined, block_start + query_min, side=side)
    start = np.searchsorted(combined, block_start, side="left")
    return (upto - start).astype("int64")


def count_in_window(event_key, event_min, query_key, query_min, window_minutes):
    """Count same-key events with time in [t - window, t)."""
    return (count_before(event_key, event_min, query_key, query_min)
            - count_before(event_key, event_min, query_key, query_min - window_minutes))


def _codes(*cols: pd.Series) -> np.ndarray:
    """Factorize one or more columns into a single non-negative int code."""
    if len(cols) == 1:
        key = cols[0].astype(str)
    else:
        key = cols[0].astype(str)
        for c in cols[1:]:
            key = key + "|" + c.astype(str)
    return pd.factorize(key)[0].astype("int64")


def add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    """Hour, weekday, month, US federal holiday, description and follow-up flags."""
    t = df["date_requested"]
    out = pd.DataFrame(index=df.index)
    out["hour"] = t.dt.hour.astype("int8")
    out["day_of_week"] = t.dt.dayofweek.astype("int8")
    out["month"] = t.dt.month.astype("int8")
    hol = USFederalHolidayCalendar().holidays(start=t.min(), end=t.max() + pd.Timedelta(days=1))
    out["is_holiday"] = t.dt.normalize().isin(hol).astype("int8")
    out["desc_len"] = df["desc_len"].astype("int32")
    out["has_description"] = (df["desc_len"] > 0).astype("int8")
    out["is_followup"] = df["service_request_parent_id"].notna().astype("int8")
    return out


def add_workload_features(df: pd.DataFrame) -> pd.DataFrame:
    """Requests submitted in [t-30d, t) for the same service (and service x neighborhood).

    Counts ALL cleaned requests, open or closed - not only closed ones.
    """
    t = to_minutes(df["date_requested"])
    win = 30 * 24 * 60
    out = pd.DataFrame(index=df.index)
    k1 = _codes(df["service_name"])
    out["workload_30d_service"] = count_in_window(k1, t, k1, t, win)
    k2 = _codes(df["service_name"], df["comm_plan_name"])
    out["workload_30d_service_comm"] = count_in_window(k2, t, k2, t, win)
    return out


def _backlog(df: pd.DataFrame, key: np.ndarray) -> np.ndarray:
    """Open backlog with the same key at the moment each request was submitted.

    Called by ``add_backlog_features`` (twice: key = service, key = council district),
    which feeds training (``build_features``) and the daily job
    (``serving.build_daily_features``). One function, both paths.

    Input
    -----
    df  : ``date_requested`` (datetime with time of day) and ``date_closed``
          (datetime at midnight = a date without time, or NaT if still open).
    key : int64 array, same length as df: a non-negative group code per row
          (from ``_codes``). Only rows with the same code count for each other.

    Output: int64 array, one value per row (same order as df). For row i with
    t = date_requested[i], count the rows j with key[j] == key[i] such that
      * j was submitted strictly before t:  date_requested[j] < t  (same minute: not before), and
      * j was NOT closed before date(t):    date_closed[j] is NaT, or date_closed[j] >= date(t).

    Tie rule (the interesting part): date_closed has no time. If j closed on the same
    calendar day that i arrived, we can't tell whether that was before or after t, so j
    still counts as open. Over-counting by a few is safe; using a closure that may lie
    in the future is leakage. A row never counts itself.

    Hints
    -----
    1. Split it: backlog = (# same-key submitted before t) - (# same-key closed before date(t)).
       Every request closed before date(t) was also submitted before t, so the subtraction
       is exact.
    2. Both parts are "count events with the same key before a time": that is
       ``count_before(event_key, event_min, query_key, query_min)`` above (strictly before).
       ``to_minutes`` turns timestamps into the integers it needs.
    3. For the closed part, the events are only the rows that HAVE a close date
       (filter key and close minutes with the same mask), and the query time is
       ``date_requested.dt.normalize()`` (midnight of t's day), not t itself.
    Write the slow double loop first if it helps you think; the tests use small frames.
    But the version you keep must be vectorised: it runs on 2.5 M rows in ~1 second.
    """
    # TODO(Jaden): your code here. Delete the raise when you are done.
    raise NotImplementedError("[lesson 2] _backlog: submitted before t minus closed before date(t)")


def add_backlog_features(df: pd.DataFrame) -> pd.DataFrame:
    """Open backlog at submission time, by service and by council district."""
    out = pd.DataFrame(index=df.index)
    out["backlog_service"] = _backlog(df, _codes(df["service_name"]))
    out["backlog_district"] = _backlog(df, _codes(df["council_district"]))
    return out


def add_bucket_mix_features(df: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Share of each bucket among this service's labels that became final in (t-90d, t].

    Only labels with ``label_final_at <= t`` are used, so the feature never
    sees an outcome that was not yet known when the request came in.
    """
    t = to_minutes(df["date_requested"])
    win = 90 * 24 * 60
    key = _codes(df["service_name"])
    final_min = to_minutes(labels["label_final_at"])
    out = pd.DataFrame(index=df.index)
    counts = {}
    for b in BUCKETS:
        m = (labels["bucket"] == b).to_numpy() & (final_min >= 0)
        ek, em = key[m], final_min[m]
        counts[b] = (count_before(ek, em, key, t, inclusive=True)
                     - count_before(ek, em, key, t - win, inclusive=True))
    n = sum(counts.values())
    names = {"0-2": "mix90_0_2", "3-7": "mix90_3_7", "8-30": "mix90_8_30", "31+": "mix90_31p"}
    with np.errstate(invalid="ignore", divide="ignore"):
        for b in BUCKETS:
            out[names[b]] = np.where(n > 0, counts[b] / np.maximum(n, 1), np.nan).astype("float32")
    out["mix90_n"] = n
    return out


def build_features(df: pd.DataFrame, as_of=DEFAULT_AS_OF, labels: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build every feature for every row of the cleaned frame ``df``.

    ``df`` is both the population of past events and the set of rows to
    score. ``as_of`` only matters for the bucket-mix feature; for any row
    with t <= as_of it gives the same result as using t itself.
    """
    if labels is None:
        labels = make_labels(df, as_of)
    out = pd.concat([
        add_calendar_features(df),
        add_workload_features(df),
        add_backlog_features(df),
        add_bucket_mix_features(df, labels),
    ], axis=1)
    out["service_name"] = df["service_name"].astype(str)
    out["case_origin"] = df["case_origin"].astype(str)
    out["case_record_type"] = df["case_record_type"].astype(str)
    out["comm_plan_name"] = df["comm_plan_name"].astype(str)
    out["council_district"] = df["council_district"].astype(str)
    out["service_x_comm"] = out["service_name"] + " | " + out["comm_plan_name"]
    for c in CATEGORICAL_FEATURES:
        out[c] = out[c].astype("category")
    return out
