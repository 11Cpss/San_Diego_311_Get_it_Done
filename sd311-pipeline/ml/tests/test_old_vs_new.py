"""Lesson 1 checker: analysis/old_vs_new.py::truncation_evidence on tiny hand-made frames.

Every expected number below can be counted by hand from the frames.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
from old_vs_new import truncation_evidence  # noqa: E402

pytestmark = pytest.mark.lesson1

KEYS = {"h2_2025_all_requests", "h2_2025_missing_from_v1_test", "h2_2025_missing_share",
        "h2_2025_missing_still_open", "q3_2025_n_proper", "q3_2025_n_v1",
        "q3_2025_bucket_share_proper", "q3_2025_bucket_share_v1_truncated"}


def proper_frame():
    """8 rows of the honest population. H2-2025 = rows 2..7 (6 rows), Q3 = rows 2..5 (4 rows)."""
    return pd.DataFrame({
        "service_request_id": ["a", "b", "c", "d", "e", "f", "g", "h"],
        "date_requested": pd.to_datetime([
            "2025-06-30 23:59",   # a: one minute before H2 -> out
            "2025-01-15 10:00",   # b: H1 -> out
            "2025-07-01 00:00",   # c: exactly H2_START -> in (Q3)
            "2025-08-10 09:00",   # d: Q3
            "2025-09-30 23:59",   # e: Q3 (last minute)
            "2025-09-05 12:00",   # f: Q3
            "2025-10-01 00:00",   # g: exactly Q3_END -> Q4 (still H2)
            "2025-12-31 23:59",   # h: last minute of H2
        ]),
        "source": ["closed", "closed", "closed", "open", "open", "closed", "open", "open"],
        "bucket": ["0-2", "31+", "0-2", "31+", "31+", "3-7", "8-30", np.nan],
    })


def v1_frame():
    """v1 test set: only closed rows (c and f) plus an id we don't care about (z)."""
    return pd.DataFrame({
        "service_request_id": ["c", "f", "z"],
        "date_requested": pd.to_datetime(["2025-07-01 00:00", "2025-09-05 12:00", "2025-11-02 08:00"]),
        "case_age_days": [1, 4, 2],
    })


def test_keys():
    assert set(truncation_evidence(proper_frame(), v1_frame())) == KEYS


def test_missing_counts_hand_computed():
    out = truncation_evidence(proper_frame(), v1_frame())
    # H2 rows: c d e f g h (6). Found in v1: c, f. Missing: d e g h (4), all "open".
    assert out["h2_2025_all_requests"] == 6
    assert out["h2_2025_missing_from_v1_test"] == 4
    assert out["h2_2025_missing_share"] == pytest.approx(4 / 6)
    assert out["h2_2025_missing_still_open"] == 4
    assert isinstance(out["h2_2025_all_requests"], int)


def test_missing_but_closed_is_not_counted_as_open():
    p = proper_frame()
    p.loc[p["service_request_id"] == "g", "source"] = "closed"   # g missing but closed
    out = truncation_evidence(p, v1_frame())
    assert out["h2_2025_missing_from_v1_test"] == 4
    assert out["h2_2025_missing_still_open"] == 3


def test_q3_counts_and_window_edges():
    out = truncation_evidence(proper_frame(), v1_frame())
    assert out["q3_2025_n_proper"] == 4          # c d e f (g is at Q3_END -> out)
    assert out["q3_2025_n_v1"] == 2              # c f (z is in November)


def test_bucket_shares_hand_computed():
    out = truncation_evidence(proper_frame(), v1_frame())
    # proper Q3 buckets: c 0-2, d 31+, e 31+, f 3-7
    assert out["q3_2025_bucket_share_proper"] == {"0-2": 0.25, "3-7": 0.25, "8-30": 0.0, "31+": 0.5}
    assert list(out["q3_2025_bucket_share_proper"]) == ["0-2", "3-7", "8-30", "31+"]
    # v1 Q3 rows: c (1 day -> 0-2), f (4 days -> 3-7). No 31+ at all: the truncation.
    assert out["q3_2025_bucket_share_v1_truncated"] == {"0-2": 0.5, "3-7": 0.5, "8-30": 0.0, "31+": 0.0}


def test_nan_buckets_are_left_out_and_shares_rounded():
    p = proper_frame()
    p.loc[p["service_request_id"] == "f", "bucket"] = np.nan    # Q3 labels now: 0-2, 31+, 31+
    out = truncation_evidence(p, v1_frame())
    assert out["q3_2025_n_proper"] == 4                          # still counted as a row
    assert out["q3_2025_bucket_share_proper"] == {"0-2": 0.3333, "3-7": 0.0, "8-30": 0.0, "31+": 0.6667}


def test_empty_windows():
    p = proper_frame()
    p = p[p["date_requested"] < "2025-07-01"]            # nothing in H2
    v1 = v1_frame().iloc[:0]
    out = truncation_evidence(p, v1)
    assert out["h2_2025_all_requests"] == 0 and out["h2_2025_missing_from_v1_test"] == 0
    assert out["h2_2025_missing_share"] == 0.0
    zero = {"0-2": 0.0, "3-7": 0.0, "8-30": 0.0, "31+": 0.0}
    assert out["q3_2025_bucket_share_proper"] == zero
    assert out["q3_2025_bucket_share_v1_truncated"] == zero


def test_inputs_not_modified():
    p, v = proper_frame(), v1_frame()
    p0, v0 = p.copy(), v.copy()
    truncation_evidence(p, v)
    pd.testing.assert_frame_equal(p, p0)
    pd.testing.assert_frame_equal(v, v0)
