"""PSI, live metrics and the promotion rule (sd311_core.monitoring).

psi and promotion_decision are yours in lesson 8 (marked lesson8): every expected value
is hand-computed. live_metrics is given code, but it calls make_labels (lesson 2).
"""
import math

import numpy as np
import pandas as pd
import pytest

from sd311_core.monitoring import (
    OTHER,
    PSI_ALARM_THRESHOLD,
    category_shares,
    drift_report,
    live_metrics,
    promotion_decision,
    psi,
    reference_distributions,
)


# ---------------------------------------------------------------- PSI
@pytest.mark.lesson8
def test_psi_zero_for_identical_mix():
    ref = {"a": 0.5, "b": 0.3, "c": 0.2}
    assert psi(ref, dict(ref)) == pytest.approx(0.0)


@pytest.mark.lesson8
def test_psi_hand_computed():
    ref, act = {"a": 0.5, "b": 0.5}, {"a": 0.8, "b": 0.2}
    expected = (0.8 - 0.5) * math.log(0.8 / 0.5) + (0.2 - 0.5) * math.log(0.2 / 0.5)
    assert psi(ref, act) == pytest.approx(expected)          # = 0.416, above the alarm
    assert psi(ref, act) > PSI_ALARM_THRESHOLD


@pytest.mark.lesson8
def test_psi_new_category_counts_as_drift():
    ref = {"Pothole": 0.6, "Graffiti": 0.4}
    act = {"Pothole": 0.5, "Graffiti": 0.3, "Brand new service": 0.2}
    assert psi(ref, act) > 0.1                                # pooled into OTHER, not ignored
    assert OTHER == "__other__"


@pytest.mark.lesson8
def test_psi_floor_for_category_missing_now():
    # b disappeared: its actual share 0 is floored at 1e-4
    ref, act = {"a": 0.5, "b": 0.5}, {"a": 1.0}
    expected = (1.0 - 0.5) * math.log(1.0 / 0.5) + (1e-4 - 0.5) * math.log(1e-4 / 0.5)
    assert psi(ref, act) == pytest.approx(expected)          # about 4.6: huge
    assert isinstance(psi(ref, act), float)


@pytest.mark.lesson8
def test_psi_pools_all_new_categories_into_one():
    ref = {"a": 0.5, "b": 0.5}
    act = {"a": 0.5, "x": 0.25, "y": 0.25}
    # pooled: OTHER = 0.5 vs reference 0 (-> 1e-4); b = 0 (-> 1e-4) vs 0.5
    pooled = 2 * (0.5 - 1e-4) * math.log(0.5 / 1e-4)
    assert psi(ref, act) == pytest.approx(pooled)
    # NOT pooled would be two separate 0.25 terms: a different, smaller number
    unpooled = 2 * (0.25 - 1e-4) * math.log(0.25 / 1e-4) + (1e-4 - 0.5) * math.log(1e-4 / 0.5)
    assert psi(ref, act) != pytest.approx(unpooled)


@pytest.mark.lesson8
def test_psi_custom_eps_and_inputs_unchanged():
    ref, act = {"a": 1.0}, {"a": 0.9, "new": 0.1}
    ref0, act0 = dict(ref), dict(act)
    expected = (0.9 - 1.0) * math.log(0.9 / 1.0) + (0.1 - 0.01) * math.log(0.1 / 0.01)
    assert psi(ref, act, eps=0.01) == pytest.approx(expected)
    assert ref == ref0 and act == act0


@pytest.mark.lesson8
def test_psi_symmetric_and_non_negative_on_same_categories():
    a, b = {"x": 0.2, "y": 0.3, "z": 0.5}, {"x": 0.4, "y": 0.4, "z": 0.2}
    assert psi(a, b) == pytest.approx(psi(b, a))
    assert psi(a, b) > 0


def test_category_shares_and_reference():
    df = pd.DataFrame({"service_name": ["a", "a", "b", None], "case_origin": ["Web"] * 4})
    assert category_shares(df["service_name"]) == {"a": 0.5, "b": 0.25, "Unknown": 0.25}
    ref = reference_distributions(df)
    assert set(ref) == {"service_name", "case_origin"} and ref["case_origin"] == {"Web": 1.0}


@pytest.mark.lesson8
def test_drift_report_max_and_none():
    ref = {"service_name": {"a": 0.5, "b": 0.5}, "case_origin": {"Web": 1.0}}
    recent = pd.DataFrame({"service_name": ["a"] * 8 + ["b"] * 2, "case_origin": ["Web"] * 10})
    rep = drift_report(recent, ref)
    assert rep["psi_by_feature"]["case_origin"] == 0
    assert rep["max_psi"] == rep["psi_by_feature"]["service_name"] > 0.25
    assert drift_report(recent.head(0), ref) is None and drift_report(recent, {}) is None


# ---------------------------------------------------------------- live metrics
def _preds(rows):
    out = pd.DataFrame(rows, columns=["service_request_id", "snapshot_date", "pred_bucket"])
    for c in ["p_0_2", "p_3_7", "p_8_30", "p_31_plus"]:
        out[c] = 0.1
    for i, b in enumerate(out["pred_bucket"]):
        out.loc[i, {"0-2": "p_0_2", "3-7": "p_3_7", "8-30": "p_8_30", "31+": "p_31_plus"}[b]] = 0.7
    return out


@pytest.mark.lesson2
def test_live_metrics_uses_only_recently_final_labels():
    as_of = "2025-06-30"
    reqs = pd.DataFrame({
        "service_request_id": ["1", "2", "3", "4"],
        "date_requested": pd.to_datetime(["2025-06-10", "2025-06-01", "2025-03-01", "2025-06-25"]),
        # 1: closed in window (0-2); 2: closed in window (8-30)
        # 3: closed long ago -> final too early; 4: still open, 5 days old -> not final
        "date_closed": pd.to_datetime(["2025-06-11", "2025-06-15", "2025-03-02", None]),
    })
    preds = _preds([("1", "2025-06-10", "0-2"), ("2", "2025-06-01", "0-2"),
                    ("3", "2025-03-01", "0-2"), ("4", "2025-06-25", "0-2")])
    m = live_metrics(preds, reqs, as_of)
    assert m["n"] == 2                                  # only ids 1 and 2
    assert 0 < m["macro_f1"] < 1 and m["log_loss"] > 0


@pytest.mark.lesson2
def test_live_metrics_first_prediction_wins_and_empty():
    reqs = pd.DataFrame({"service_request_id": ["1"], "date_requested": pd.to_datetime(["2025-06-10"]),
                         "date_closed": pd.to_datetime(["2025-06-11"])})
    preds = _preds([("1", "2025-06-11", "31+"), ("1", "2025-06-10", "0-2")])
    assert live_metrics(preds, reqs, "2025-06-30")["macro_f1"] > 0   # the 0-2 (first) one, correct
    assert live_metrics(preds.head(0), reqs, "2025-06-30") == {"macro_f1": None, "log_loss": None, "n": 0}
    # nothing final yet (as_of before the close day) -> None, never 0
    assert live_metrics(preds, reqs, "2025-06-10")["macro_f1"] is None


# ---------------------------------------------------------------- promotion rule
def fold(model_f1, base_f1, ll):
    return {"model": {"macro_f1": model_f1, "log_loss": ll}, "baseline": {"macro_f1": base_f1}}


@pytest.mark.lesson8
def test_promote_when_better_and_beats_baseline_everywhere():
    d = promotion_decision([fold(0.53, 0.42, 0.90), fold(0.52, 0.40, 0.92)], champion_mean_log_loss=0.95)
    assert d["alias"] == "champion" and d["mean_log_loss"] == pytest.approx(0.91)


@pytest.mark.lesson8
def test_first_model_becomes_champion_if_it_beats_baseline():
    assert promotion_decision([fold(0.5, 0.4, 1.0)], None)["alias"] == "champion"


@pytest.mark.lesson8
def test_challenger_when_one_fold_loses_to_baseline():
    d = promotion_decision([fold(0.53, 0.42, 0.80), fold(0.39, 0.40, 0.80)], champion_mean_log_loss=0.95)
    assert d["alias"] == "challenger" and "fold 1" in d["reason"]


@pytest.mark.lesson8
def test_challenger_when_log_loss_not_better():
    d = promotion_decision([fold(0.53, 0.42, 0.95)], champion_mean_log_loss=0.95)   # tie is not better
    assert d["alias"] == "challenger" and "log-loss" in d["reason"]


@pytest.mark.lesson8
def test_no_folds_is_challenger():
    assert promotion_decision([], None)["alias"] == "challenger"


@pytest.mark.lesson8
def test_mean_is_over_folds():
    d = promotion_decision([fold(0.6, 0.4, 0.7), fold(0.6, 0.4, 1.3)], champion_mean_log_loss=1.01)
    assert d["mean_log_loss"] == pytest.approx(np.mean([0.7, 1.3]))
    assert d["alias"] == "champion"


@pytest.mark.lesson8
def test_decision_fields_hand_computed():
    d = promotion_decision([fold(0.50, 0.40, 0.9), fold(0.45, 0.42, 1.0), fold(0.60, 0.40, 0.8)], 0.95)
    assert d["alias"] == "champion"
    assert d["beats_baseline_every_fold"] is True
    assert d["min_f1_margin"] == pytest.approx(0.03)          # fold 1: 0.45 - 0.42
    assert d["mean_log_loss"] == pytest.approx(0.9)
    assert "log-loss" in d["reason"] or "champion" in d["reason"]


@pytest.mark.lesson8
def test_tie_with_baseline_is_not_a_win():
    d = promotion_decision([fold(0.50, 0.40, 0.8), fold(0.40, 0.40, 0.8)], None)
    assert d["alias"] == "challenger" and d["beats_baseline_every_fold"] is False
    assert d["min_f1_margin"] == pytest.approx(0.0) and "fold 1" in d["reason"]


@pytest.mark.lesson8
def test_reason_names_the_worst_fold():
    d = promotion_decision([fold(0.39, 0.40, 0.8), fold(0.50, 0.40, 0.8), fold(0.30, 0.40, 0.8)], None)
    assert d["alias"] == "challenger" and "fold 2" in d["reason"]     # margin -0.10 is the worst
    assert d["min_f1_margin"] == pytest.approx(-0.10)


@pytest.mark.lesson8
def test_both_failures_are_explained():
    d = promotion_decision([fold(0.39, 0.40, 1.2)], champion_mean_log_loss=0.95)
    assert d["alias"] == "challenger"
    assert "fold 0" in d["reason"] and "log-loss" in d["reason"]


@pytest.mark.lesson8
def test_better_log_loss_alone_is_not_enough_and_no_folds_shape():
    assert promotion_decision([fold(0.41, 0.42, 0.10)], champion_mean_log_loss=0.95)["alias"] == "challenger"
    d = promotion_decision([], 0.9)
    assert d == {"alias": "challenger", "mean_log_loss": None,
                 "beats_baseline_every_fold": False, "reason": "no backtest folds"}
