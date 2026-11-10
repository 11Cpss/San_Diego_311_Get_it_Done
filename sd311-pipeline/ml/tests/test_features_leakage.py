"""Lesson 2 checker: leakage tests and the backlog (features at t must not use anything after t).

build_features calls make_labels and _backlog, which you write in lesson 2, so the
whole module is marked lesson2.
"""
import numpy as np
import pandas as pd
import pytest
from helpers import make_requests

from sd311_core.features import FEATURES, _backlog, _codes, build_features

pytestmark = pytest.mark.lesson2

AS_OF = pd.Timestamp("2025-12-31")


def small_history():
    return make_requests([
        {"date_requested": "2025-01-01 08:00", "date_closed": "2025-01-02"},
        {"date_requested": "2025-01-05 08:00", "date_closed": "2025-03-01"},
        {"date_requested": "2025-01-10 08:00", "service_name": "Graffiti", "date_closed": "2025-01-10"},
        {"date_requested": "2025-02-01 08:00"},                                  # never closed
        {"date_requested": "2025-02-10 12:00", "date_closed": "2025-02-20"},     # row we inspect
    ])


def features_of_first_rows(df, n):
    f = build_features(df, AS_OF).iloc[:n]
    return f[FEATURES].reset_index(drop=True)


def test_future_request_does_not_change_past_features():
    df = small_history()
    before = features_of_first_rows(df, len(df))
    future = make_requests([{"date_requested": "2025-02-10 12:01", "date_closed": "2025-02-10"},
                            {"date_requested": "2025-06-01 00:00"}])
    future["service_request_id"] = ["98", "99"]
    after = features_of_first_rows(pd.concat([df, future], ignore_index=True), len(df))
    pd.testing.assert_frame_equal(before, after)


def test_request_closed_after_t_counts_in_backlog():
    df = small_history()
    f = build_features(df, AS_OF)
    # Row 5 (2025-02-10): earlier Pothole requests are #1 (closed 01-02),
    # #2 (closes 03-01, i.e. AFTER t) and #4 (never closed) -> backlog 2.
    assert f["backlog_service"].iloc[4] == 2
    # Changing #2's close date to a still-later day must not change anything at t.
    df2 = df.copy()
    df2.loc[1, "date_closed"] = pd.Timestamp("2025-09-01")
    assert build_features(df2, AS_OF)["backlog_service"].iloc[4] == 2


def test_same_day_close_still_counts_as_open():
    df = make_requests([
        {"date_requested": "2025-03-01 08:00", "date_closed": "2025-03-01"},
        {"date_requested": "2025-03-01 15:00"},
    ])
    # Request 1 closed "on 03-01" but we do not know the time, so at 15:00 it is still backlog.
    assert build_features(df, AS_OF)["backlog_service"].iloc[1] == 1


def test_workload_window_is_half_open():
    df = make_requests([
        {"date_requested": "2025-03-01 08:00"},
        {"date_requested": "2025-03-31 08:00"},   # exactly 30 days later: t-30d is INCLUDED
        {"date_requested": "2025-03-31 08:00"},   # same minute as row 2: row 2 NOT counted (< t)
        {"date_requested": "2025-03-31 08:01"},   # first row is now just outside the window
    ])
    w = build_features(df, AS_OF)["workload_30d_service"].to_numpy()
    assert list(w) == [0, 1, 1, 2]


def test_bucket_mix_only_uses_final_labels():
    df = make_requests([
        {"date_requested": "2025-03-01 08:00", "date_closed": "2025-03-02"},  # final 03-03 00:00
        {"date_requested": "2025-03-02 12:00"},                               # label not final yet
        {"date_requested": "2025-03-03 00:00"},                               # now it is
    ])
    f = build_features(df, AS_OF)
    assert f["mix90_n"].iloc[1] == 0 and np.isnan(f["mix90_0_2"].iloc[1])
    assert f["mix90_n"].iloc[2] == 1 and f["mix90_0_2"].iloc[2] == 1.0


# ---------------------------------------------------------------- _backlog directly
def backlog(rows, key="service_name"):
    df = make_requests(rows)
    return _backlog(df, _codes(df[key])).tolist()


@pytest.mark.parametrize("rows, expected", [
    pytest.param([{"date_requested": "2025-01-01 08:00"}], [0], id="nothing_before"),
    pytest.param([{"date_requested": "2025-01-01 08:00"}, {"date_requested": "2025-01-02 08:00"}],
                 [0, 1], id="one_open_before"),
    pytest.param([{"date_requested": "2025-01-01 08:00", "date_closed": "2025-01-01"},
                  {"date_requested": "2025-01-02 08:00"}], [0, 0], id="closed_earlier_day"),
    pytest.param([{"date_requested": "2025-01-01 08:00", "date_closed": "2025-01-02"},
                  {"date_requested": "2025-01-02 18:00"}], [0, 1], id="closed_same_day_counts_as_open"),
    pytest.param([{"date_requested": "2025-01-01 08:00", "date_closed": "2025-03-01"},
                  {"date_requested": "2025-01-02 08:00"}], [0, 1], id="closed_later_counts_as_open"),
    pytest.param([{"date_requested": "2025-01-01 08:00"}, {"date_requested": "2025-01-01 08:00"}],
                 [0, 0], id="same_minute_not_before"),
    pytest.param([{"date_requested": "2025-01-01 08:00", "service_name": "Graffiti"},
                  {"date_requested": "2025-01-02 08:00"}], [0, 0], id="other_service_ignored"),
    pytest.param([{"date_requested": "2025-01-01 08:00", "date_closed": "2025-01-03"},
                  {"date_requested": "2025-01-02 09:00", "service_name": "Graffiti"},
                  {"date_requested": "2025-01-02 10:00"},
                  {"date_requested": "2025-01-04 10:00", "date_closed": "2025-01-04"},
                  {"date_requested": "2025-01-05 10:00"}], [0, 0, 1, 1, 1], id="mixed"),
])
def test_backlog_hand_computed(rows, expected):
    assert backlog(rows) == expected


def test_backlog_rows_need_not_be_sorted():
    # the "mixed" case above, rows given in reverse order: answers follow their rows
    rows = [{"date_requested": "2025-01-05 10:00"},
            {"date_requested": "2025-01-04 10:00", "date_closed": "2025-01-04"},
            {"date_requested": "2025-01-02 10:00"},
            {"date_requested": "2025-01-02 09:00", "service_name": "Graffiti"},
            {"date_requested": "2025-01-01 08:00", "date_closed": "2025-01-03"}]
    assert backlog(rows) == [1, 1, 1, 0, 0]


def test_backlog_by_district_key():
    rows = [{"date_requested": "2025-01-01 08:00", "council_district": "1", "service_name": "A"},
            {"date_requested": "2025-01-01 09:00", "council_district": "1", "service_name": "B"},
            {"date_requested": "2025-01-01 10:00", "council_district": "2", "service_name": "A"}]
    assert backlog(rows, key="council_district") == [0, 1, 0]
    assert backlog(rows, key="service_name") == [0, 0, 1]


def test_backlog_never_closed_equals_rank():
    # 50 requests of one service, all still open, one per hour, given shuffled:
    # the k-th oldest sees exactly k-1 open requests before it.
    rng = np.random.default_rng(0)
    order = rng.permutation(50)
    times = pd.Timestamp("2025-02-01") + pd.to_timedelta(order, unit="h")
    out = backlog([{"date_requested": str(t)} for t in times])
    assert out == order.tolist()


def test_backlog_closed_same_day_each_day():
    # one request per day at 10:00, each closed on its own day: at day d, all earlier ones
    # closed on an earlier calendar day -> backlog 0 every time.
    rows = [{"date_requested": f"2025-04-{d:02d} 10:00", "date_closed": f"2025-04-{d:02d}"}
            for d in range(1, 11)]
    assert backlog(rows) == [0] * 10


def test_backlog_returns_int_array_per_row():
    df = make_requests([{"date_requested": "2025-01-01 08:00"}, {"date_requested": "2025-01-03 08:00"}])
    out = _backlog(df, _codes(df["service_name"]))
    assert isinstance(out, np.ndarray) and out.dtype == np.int64 and out.tolist() == [0, 1]
