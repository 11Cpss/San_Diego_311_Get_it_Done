"""Lesson 3 checker: backtest.make_fold and baseline.fit_baseline (hand-computed values).

Labels here are written by hand (bucket + label_final_at), so these tests do not
depend on lesson 2.
"""
import numpy as np
import pandas as pd
import pytest

from sd311_core.backtest import EMBARGO_DAYS, Fold, make_fold
from sd311_core.baseline import fit_baseline, predict_baseline

pytestmark = pytest.mark.lesson3

T = pd.Timestamp


# ---------------------------------------------------------------- make_fold
def test_fold_matches_the_reported_2025_07_fold():
    # the same dates as reports/backtest.json for the 2025-07 fold
    f = make_fold("2025-07-01")
    assert isinstance(f, Fold)
    assert f.as_dict() == {"train_start": "2023-07-01", "valid_start": "2025-05-01",
                           "valid_end": "2025-05-31", "test_start": "2025-07-01",
                           "test_end": "2025-10-01"}


@pytest.mark.parametrize("origin, train_start, valid_start, valid_end, test_end", [
    ("2024-07-01", "2022-07-01", "2024-05-01", "2024-05-31", "2024-10-01"),
    ("2025-01-01", "2023-01-01", "2024-11-01", "2024-12-01", "2025-04-01"),
    ("2025-04-01", "2023-04-01", "2025-01-30", "2025-03-01", "2025-07-01"),   # February is short
])
def test_fold_dates_of_the_backtest_table(origin, train_start, valid_start, valid_end, test_end):
    f = make_fold(origin)
    assert f.as_dict() == {"train_start": train_start, "valid_start": valid_start,
                           "valid_end": valid_end, "test_start": origin, "test_end": test_end}


def test_fold_fields_are_timestamps_and_accepts_timestamp():
    f = make_fold(T("2024-10-01"))
    for v in (f.train_start, f.valid_start, f.valid_end, f.test_start, f.test_end):
        assert isinstance(v, pd.Timestamp)
    assert f.as_dict() == {"train_start": "2022-10-01", "valid_start": "2024-08-01",
                           "valid_end": "2024-08-31", "test_start": "2024-10-01",
                           "test_end": "2025-01-01"}


def test_fold_custom_lengths_leap_year():
    f = make_fold("2024-03-01", train_months=12, test_months=1)
    assert f.train_start == T("2023-03-01")
    assert f.valid_end == T("2024-01-30")      # 31 days before 2024-03-01 (Feb 2024 has 29 days)
    assert f.valid_start == T("2023-12-31")    # 30 days before valid_end
    assert f.test_end == T("2024-04-01")


def test_fold_month_end_origin_uses_calendar_months():
    f = make_fold("2024-05-31", train_months=24, test_months=3)
    assert f.train_start == T("2022-05-31")
    assert f.test_end == T("2024-08-31")
    assert f.valid_end == T("2024-04-30")


def test_embargo_property():
    for origin in ["2024-07-01", "2024-10-01", "2025-01-01", "2025-04-01", "2025-07-01", "2026-01-01"]:
        f = make_fold(origin)
        assert f.test_start - f.valid_end == pd.Timedelta(days=EMBARGO_DAYS) == pd.Timedelta(days=31)
        assert f.valid_end - f.valid_start == pd.Timedelta(days=30)
        assert f.train_start < f.valid_start < f.valid_end < f.test_start < f.test_end
        # the newest validation request (just before valid_end) is >= 31 days old at T,
        # so its label was final when the fold is "run"
        newest_valid = f.valid_end - pd.Timedelta(minutes=1)
        assert newest_valid + pd.Timedelta(days=31) < f.test_start


# ---------------------------------------------------------------- fit_baseline
START = T("2025-07-01")


def labels_frame(rows):
    """rows: (service, bucket, label_final_at, count)"""
    out = []
    for svc, bucket, final_at, n in rows:
        out += [{"service_name": svc, "bucket": bucket, "label_final_at": T(final_at) if final_at else pd.NaT}] * n
    df = pd.DataFrame(out)
    df["label_final_at"] = pd.to_datetime(df["label_final_at"])
    return df


def test_baseline_mix_hand_computed():
    df = labels_frame([
        ("Pothole", "0-2", "2025-06-10", 20),
        ("Pothole", "31+", "2025-05-01", 10),     # 30 rows -> own mix
        ("Graffiti", "3-7", "2025-06-20", 29),    # 29 rows -> too few, falls back
        ("Graffiti", "8-30", "2025-06-21", 0),
    ])
    m = fit_baseline(df, START)
    assert set(m["by_service"]) == {"Pothole"}
    np.testing.assert_allclose(m["by_service"]["Pothole"], [20 / 30, 0, 0, 10 / 30])
    # global uses EVERY known row, including Graffiti's 29
    np.testing.assert_allclose(m["global"], [20 / 59, 29 / 59, 0, 10 / 59])
    assert m["period_start"] == "2025-07-01"


def test_baseline_window_edges():
    df = labels_frame([
        ("A", "0-2", "2025-07-01 00:00", 1),      # == T: known at T -> in
        ("A", "3-7", "2025-07-01 00:01", 1),      # after T: the future -> out
        ("A", "8-30", "2025-04-02 00:00", 1),     # == T - 90d: left end open -> out
        ("A", "31+", "2025-04-02 00:01", 1),      # just inside -> in
        ("A", "3-7", None, 1),                    # never final -> out
    ])
    m = fit_baseline(df, START, min_count=1)
    np.testing.assert_allclose(m["by_service"]["A"], [0.5, 0, 0, 0.5])
    np.testing.assert_allclose(m["global"], [0.5, 0, 0, 0.5])


def test_baseline_ignores_missing_bucket_and_custom_params():
    df = labels_frame([
        ("A", "0-2", "2025-06-30", 2),
        ("A", np.nan, "2025-06-30", 5),           # no bucket -> ignored
        ("A", "8-30", "2025-03-15", 2),           # 108 days before T: only in a 120-day window
    ])
    m90 = fit_baseline(df, START, min_count=2)
    np.testing.assert_allclose(m90["by_service"]["A"], [1, 0, 0, 0])
    m120 = fit_baseline(df, START, window_days=120, min_count=4)
    np.testing.assert_allclose(m120["by_service"]["A"], [0.5, 0, 0.5, 0])
    assert fit_baseline(df, START, window_days=120, min_count=5)["by_service"] == {}


def test_baseline_keys_are_strings_and_rows_sum_to_one():
    df = labels_frame([("Pothole", "0-2", "2025-06-01", 30), ("Pothole", "3-7", "2025-06-02", 10)])
    df["service_name"] = df["service_name"].astype("category")
    m = fit_baseline(df, "2025-07-01")
    assert list(m["by_service"]) == ["Pothole"] and isinstance(list(m["by_service"])[0], str)
    for arr in [m["global"], *m["by_service"].values()]:
        assert len(arr) == 4 and arr.sum() == pytest.approx(1.0)


def test_baseline_end_to_end_prediction():
    df = labels_frame([
        ("Pothole", "31+", "2025-06-10", 25), ("Pothole", "0-2", "2025-06-10", 5),
        ("Graffiti", "0-2", "2025-06-10", 40),
    ])
    m = fit_baseline(df, START)
    pred, proba = predict_baseline(m, pd.Series(["Pothole", "Graffiti", "Brand new service"]))
    assert pred.tolist() == ["31+", "0-2", "0-2"]          # unknown service -> global mix (45/70 "0-2")
    np.testing.assert_allclose(proba[2], [45 / 70, 0, 0, 25 / 70])
