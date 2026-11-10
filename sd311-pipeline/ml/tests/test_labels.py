"""Lesson 2 checker: edge cases for sd311_core.labels.make_labels (hand-computed values)."""
import numpy as np
import pandas as pd
import pytest
from helpers import make_requests

from sd311_core.labels import make_labels

pytestmark = pytest.mark.lesson2

AS_OF = pd.Timestamp("2025-12-31")


def bucket_for(requested, closed=None):
    df = make_requests([{"date_requested": requested, "date_closed": closed}])
    return make_labels(df, AS_OF)["bucket"].iloc[0]


@pytest.mark.parametrize("days, expected", [
    (0, "0-2"), (2, "0-2"), (3, "3-7"), (7, "3-7"),
    (8, "8-30"), (30, "8-30"), (31, "31+"), (400, "31+"),
])
def test_closed_bucket_boundaries(days, expected):
    req = pd.Timestamp("2025-03-01 23:59")  # late in the day: whole calendar days still count
    closed = req.normalize() + pd.Timedelta(days=days)
    assert bucket_for(req, closed) == expected


def test_open_younger_than_31_days_is_unlabeled():
    # 30 days old at as_of: it might still close within 30 days -> unknown
    assert pd.isna(bucket_for("2025-12-01 10:00"))


def test_open_31_days_or_older_is_31_plus():
    assert bucket_for("2025-11-30 10:00") == "31+"   # exactly 31 days at as_of
    assert bucket_for("2020-01-01") == "31+"


def test_closed_after_as_of_treated_like_open():
    # closed after as_of: we pretend we have not seen the close yet
    assert pd.isna(bucket_for("2025-12-20", "2026-01-05"))          # young -> unlabeled
    assert bucket_for("2025-11-01", "2026-01-05") == "31+"          # old -> 31+


def test_label_final_at():
    df = make_requests([
        {"date_requested": "2025-05-01 09:00", "date_closed": "2025-05-03"},  # closed
        {"date_requested": "2025-05-01 09:00"},                               # open, old
        {"date_requested": "2025-12-25 09:00"},                               # open, young
    ])
    lab = make_labels(df, AS_OF)
    assert lab["label_final_at"].iloc[0] == pd.Timestamp("2025-05-04")        # end of close day
    assert lab["label_final_at"].iloc[1] == pd.Timestamp("2025-06-01 09:00")  # requested + 31d
    assert pd.isna(lab["label_final_at"].iloc[2])


def test_late_closed_31_plus_is_final_at_day_31_not_at_close():
    """A request that closed after day 31 was certainly "31+" from day 31 on.

    Before the fix its label_final_at was the close date (+1), so the offline
    bucket-mix feature counted it later than the daily serving job did
    (train/serve skew, lesson 08).
    """
    df = make_requests([
        {"date_requested": "2025-05-01 09:00", "date_closed": "2025-08-20"},  # 111 days
        {"date_requested": "2025-05-01 09:00", "date_closed": "2025-06-01"},  # exactly 31 days
        {"date_requested": "2025-05-01 09:00", "date_closed": "2025-05-31"},  # 30 days -> 8-30
    ])
    lab = make_labels(df, AS_OF)
    assert lab["bucket"].tolist() == ["31+", "31+", "8-30"]
    assert lab["label_final_at"].iloc[0] == pd.Timestamp("2025-06-01 09:00")
    assert lab["label_final_at"].iloc[1] == pd.Timestamp("2025-06-01 09:00")
    assert lab["label_final_at"].iloc[2] == pd.Timestamp("2025-06-01")        # end of close day
    # Same answer whatever as_of is, once the label is final (no dependence on as_of):
    lab_early = make_labels(df, pd.Timestamp("2025-06-15"))
    assert lab_early["label_final_at"].iloc[0] == lab["label_final_at"].iloc[0]


def test_output_shape_index_and_columns():
    df = make_requests([
        {"date_requested": "2025-05-01 09:00", "date_closed": "2025-05-03"},
        {"date_requested": "2025-12-25 09:00"},
    ])
    df.index = [10, 42]                                    # non-default index must be kept
    lab = make_labels(df, AS_OF)
    assert list(lab.columns) == ["days_to_close", "bucket", "label_final_at"]
    assert list(lab.index) == [10, 42]
    assert lab.loc[10, "days_to_close"] == 2.0 and lab.loc[10, "bucket"] == "0-2"
    assert np.isnan(lab.loc[42, "days_to_close"]) and pd.isna(lab.loc[42, "bucket"])
    assert pd.api.types.is_datetime64_any_dtype(lab["label_final_at"])


def test_days_use_calendar_day_of_request():
    # requested 23:59, closed the next calendar day -> 1 day (not 0.0007 days, not 0)
    df = make_requests([{"date_requested": "2025-03-01 23:59", "date_closed": "2025-03-02"}])
    assert make_labels(df, AS_OF)["days_to_close"].iloc[0] == 1.0


def test_closed_on_as_of_is_known_closed_day_after_is_not():
    df = make_requests([
        {"date_requested": "2025-12-29 10:00", "date_closed": "2025-12-31"},   # closed ON as_of
        {"date_requested": "2025-12-29 10:00", "date_closed": "2026-01-01"},   # one day after
    ])
    lab = make_labels(df, AS_OF)
    assert lab["bucket"].iloc[0] == "0-2" and lab["days_to_close"].iloc[0] == 2.0
    assert lab["label_final_at"].iloc[0] == pd.Timestamp("2026-01-01")
    assert pd.isna(lab["bucket"].iloc[1]) and np.isnan(lab["days_to_close"].iloc[1])
    assert pd.isna(lab["label_final_at"].iloc[1])


def test_as_of_time_of_day_is_ignored():
    df = make_requests([
        {"date_requested": "2025-11-30 10:00"},                              # 31 days at 12-31
        {"date_requested": "2025-12-29 10:00", "date_closed": "2025-12-31"},
    ])
    a = make_labels(df, pd.Timestamp("2025-12-31 00:00"))
    b = make_labels(df, pd.Timestamp("2025-12-31 18:30"))
    pd.testing.assert_frame_equal(a, b)
    assert a["bucket"].tolist() == ["31+", "0-2"]


def test_open_exactly_30_and_31_days_and_final_at():
    df = make_requests([
        {"date_requested": "2025-12-01 23:00"},     # 30 days at as_of -> unknown
        {"date_requested": "2025-11-30 23:00"},     # 31 days at as_of -> 31+
    ])
    lab = make_labels(df, AS_OF)
    assert pd.isna(lab["bucket"].iloc[0]) and pd.isna(lab["label_final_at"].iloc[0])
    assert lab["bucket"].iloc[1] == "31+"
    assert lab["label_final_at"].iloc[1] == pd.Timestamp("2025-12-31 23:00")   # keeps the time of day
    assert np.isnan(lab["days_to_close"].iloc[1])                               # open: no days


def test_closed_after_as_of_and_old_is_final_at_day_31():
    df = make_requests([{"date_requested": "2025-11-01 08:00", "date_closed": "2026-01-05"}])
    lab = make_labels(df, AS_OF)
    assert lab["bucket"].iloc[0] == "31+"
    assert np.isnan(lab["days_to_close"].iloc[0])                               # close not seen yet
    assert lab["label_final_at"].iloc[0] == pd.Timestamp("2025-12-02 08:00")


def test_mixed_frame_all_rules_at_once():
    df = make_requests([
        {"date_requested": "2025-06-01 08:00", "date_closed": "2025-06-08"},   # 7 days -> 3-7
        {"date_requested": "2025-06-01 08:00", "date_closed": "2025-06-09"},   # 8 days -> 8-30
        {"date_requested": "2025-06-01 08:00", "date_closed": "2025-07-15"},   # 44 days -> 31+
        {"date_requested": "2025-06-01 08:00"},                                # open, old -> 31+
        {"date_requested": "2025-12-20 08:00"},                                # open, young -> NaN
    ])
    lab = make_labels(df, AS_OF)
    assert lab["bucket"].tolist()[:4] == ["3-7", "8-30", "31+", "31+"] and pd.isna(lab["bucket"].iloc[4])
    assert lab["days_to_close"].tolist()[:3] == [7.0, 8.0, 44.0]
    assert lab["label_final_at"].tolist()[:4] == [
        pd.Timestamp("2025-06-09"), pd.Timestamp("2025-06-10"),
        pd.Timestamp("2025-07-02 08:00"), pd.Timestamp("2025-07-02 08:00")]
