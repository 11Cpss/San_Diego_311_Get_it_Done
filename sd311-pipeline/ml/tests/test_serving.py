"""Daily features + scoring (sd311_core.serving) on small synthetic frames.

These test serving code, not your lesson code directly, but every feature build calls
make_labels and _backlog (lesson 2). They are marked lesson2 so `lessons.py check 2`
also proves the daily path works with your functions; until then they are skipped.
"""
import numpy as np
import pandas as pd
import pytest

from sd311_core.features import FEATURES, build_features
from sd311_core.serving import (
    PREDICTION_COLUMNS,
    build_daily_features,
    features_for_storage,
    history_mask,
    prepare_requests,
    score_frame,
)

SNAP = pd.Timestamp("2025-06-30")


def synthetic_requests(n=3000, seed=0):
    """Requests over 300 days before SNAP, a mix of closed and still-open ones."""
    rng = np.random.default_rng(seed)
    req = SNAP - pd.to_timedelta(rng.integers(1, 300 * 24 * 60, n), unit="min")
    days = rng.choice([0, 1, 5, 12, 60], size=n)
    closed = (req.normalize() + pd.to_timedelta(days, unit="D"))
    closed = pd.Series(closed).where(pd.Series(closed) < SNAP)  # not closed yet -> NaT
    return pd.DataFrame({
        "service_request_id": [str(100000 + i) for i in range(n)],
        "service_request_parent_id": None,
        "date_requested": req.astype(str),
        "date_closed": closed.dt.strftime("%Y-%m-%d"),
        "case_age_days": "1",
        "case_record_type": "TSW",
        "service_name": rng.choice(["Pothole", "Graffiti - Public", "Streetlight"], n),
        "status": np.where(closed.notna(), "Closed", "In Process"),
        "council_district": rng.choice(["1", "2", "3.0"], n),
        "comm_plan_name": rng.choice(["uptown", "Mid-City"], n),
        "case_origin": rng.choice(["Mobile", "Web"], n),
        "desc_len": rng.choice([0, 25, 120], n),
    })


@pytest.mark.lesson2
def test_history_window_gives_same_features_as_full_history():
    """The bounded window must not change any feature of today's new requests."""
    full = prepare_requests(synthetic_requests(), "closed")
    new_ids = full.sort_values("date_requested").tail(20)["service_request_id"]
    new = full[full["service_request_id"].isin(new_ids)]
    hist = full[~full["service_request_id"].isin(new_ids)]

    windowed = build_daily_features(hist[history_mask(hist, SNAP)], new, SNAP)
    unbounded = build_daily_features(hist, new, SNAP)
    assert len(windowed) == 20
    assert history_mask(hist, SNAP).mean() < 1   # the window really drops rows
    pd.testing.assert_frame_equal(windowed, unbounded)


@pytest.mark.lesson2
def test_daily_features_match_offline_build_features():
    """Same numbers as the training code path (build_features on everything)."""
    from sd311_core.cleaning import clean_requests

    full = prepare_requests(synthetic_requests(), "closed")
    new_ids = set(full.sort_values("date_requested").tail(10)["service_request_id"])
    daily = build_daily_features(full[~full["service_request_id"].isin(new_ids)],
                                 full[full["service_request_id"].isin(new_ids)], SNAP)
    cleaned = clean_requests(full)
    offline = build_features(cleaned, as_of=SNAP)
    offline.insert(0, "service_request_id", cleaned["service_request_id"].to_numpy())
    offline = offline[offline["service_request_id"].isin(new_ids)]
    daily = daily.set_index("service_request_id").sort_index()
    offline = offline.set_index("service_request_id").sort_index()
    num = [c for c in FEATURES if c not in ("service_name", "case_origin", "case_record_type",
                                            "comm_plan_name", "council_district", "service_x_comm")]
    pd.testing.assert_frame_equal(daily[num], offline[num])


@pytest.mark.lesson2
def test_new_rows_use_first_seen_values():
    """A later edit in the history copy must not reach today's features."""
    full = prepare_requests(synthetic_requests(200), "closed")
    new = full.tail(1).copy()
    hist = full.copy()
    hist.loc[hist.index[-1], "service_name"] = "Streetlight-EDITED"
    out = build_daily_features(hist, new, SNAP)
    assert len(out) == 1 and "EDITED" not in str(out["service_name"].iloc[0])


@pytest.mark.lesson2
def test_storage_frame_has_no_categoricals():
    full = prepare_requests(synthetic_requests(300), "closed")
    out = features_for_storage(build_daily_features(full.head(250), full.tail(50), SNAP))
    assert not any(str(t) == "category" for t in out.dtypes)


@pytest.mark.lesson2
def test_score_frame_shape():
    pytest.importorskip("catboost")
    from sd311_core.labels import make_labels
    from sd311_core.train import fit_model

    full = prepare_requests(synthetic_requests(1500), "closed")
    from sd311_core.cleaning import clean_requests
    df = clean_requests(full)
    data = build_features(df, as_of=SNAP).join(make_labels(df, SNAP))
    data = data[data["bucket"].notna()]
    model = fit_model(data, params={"iterations": 20, "verbose": 0, "early_stopping_rounds": None})

    feats = build_daily_features(full.head(1400), full.tail(100), SNAP)
    preds = score_frame(model, feats, SNAP, model_version="7")
    assert list(preds.columns) == PREDICTION_COLUMNS
    assert len(preds) == len(feats)
    assert set(preds["pred_bucket"]) <= {"0-2", "3-7", "8-30", "31+"}
    assert np.allclose(preds[["p_0_2", "p_3_7", "p_8_30", "p_31_plus"]].sum(axis=1), 1.0)
    assert (preds["model_version"] == "7").all()
    assert score_frame(model, feats.head(0), SNAP, "7").columns.tolist() == PREDICTION_COLUMNS


# --------------------------------------------------------------------------
# Regression test for the train/serve skew found during integration (lesson 08).
#
# Offline training builds features ONCE, with labels as of a late date
# (2025-12-31), from data that already contains every later closure. The daily
# job builds features for day t from what was known on day t. For every request
# submitted on day t both paths must give the same numbers.
#
# The old test above used as_of = SNAP for both paths, so it could not see the
# bug: a slow request that closed AFTER day 31 counted as "31+" from day 31 in
# the daily path, but only from its close date in the offline path.
# --------------------------------------------------------------------------
def _world(seed=1, n=4000):
    """Requests from 300 days before SNAP to 60 days after; closures may be far later."""
    rng = np.random.default_rng(seed)
    req = (SNAP - pd.Timedelta(days=300)
           + pd.to_timedelta(rng.integers(0, 360 * 24 * 60, n), unit="min"))
    days = rng.choice([0, 1, 5, 12, 31, 45, 90, 200], size=n)   # many slow (31+) closures
    closed = pd.Series(req.normalize() + pd.to_timedelta(days, unit="D"))
    closed = closed.where(rng.random(n) > 0.05)                   # 5% never close
    return pd.DataFrame({
        "service_request_id": [str(500000 + i) for i in range(n)],
        "service_request_parent_id": None,
        "date_requested": pd.Series(req),
        "date_closed": closed,
        "case_age_days": 1,
        "case_record_type": "TSW",
        "service_name": rng.choice(["Pothole", "Graffiti - Public", "Streetlight"], n),
        "status": np.where(closed.notna(), "Closed", "In Process"),
        "council_district": rng.choice(["1", "2", "3"], n),
        "comm_plan_name": rng.choice(["Uptown", "Mid-City"], n),
        "case_origin": rng.choice(["Mobile", "Web"], n),
        "desc_len": rng.choice([0, 7, 40, 300], size=n),
    })


def _state_on(world: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """What the daily tables know on ``day``: submitted before it, closures before it."""
    seen = world[world["date_requested"] < day].copy()
    later = seen["date_closed"] >= day
    seen.loc[later, "date_closed"] = pd.NaT
    seen.loc[later, "status"] = "In Process"
    return seen


@pytest.mark.lesson2
@pytest.mark.parametrize("day", ["2025-03-03", "2025-06-30", "2025-08-15"])
def test_offline_training_features_equal_daily_serving_features(day):
    from sd311_core.cleaning import clean_requests
    from sd311_core.labels import DEFAULT_AS_OF

    day = pd.Timestamp(day)
    world = _world()
    # Offline (training): every row, all closures known, labels as of 2025-12-31.
    cleaned = clean_requests(world.assign(source="closed"))
    offline = build_features(cleaned, as_of=DEFAULT_AS_OF)
    offline.insert(0, "service_request_id", cleaned["service_request_id"].to_numpy())

    # Online (serving on `day`): history = state known that day, new rows = first seen that day.
    on_day = world["date_requested"].dt.normalize() == day
    new = world[on_day].assign(date_closed=pd.NaT, status="New")
    hist = _state_on(world, day)
    daily = build_daily_features(prepare_requests(hist, "closed"), prepare_requests(new, "open"), day)

    assert len(daily) == on_day.sum() > 0
    offline = offline[offline["service_request_id"].isin(daily["service_request_id"])]
    daily = daily.set_index("service_request_id").sort_index()
    offline = offline.set_index("service_request_id").sort_index()
    num = [c for c in FEATURES if c not in ("service_name", "case_origin", "case_record_type",
                                            "comm_plan_name", "council_district", "service_x_comm")]
    # make sure the test exercises slow requests in the 90-day mix window
    assert (offline["mix90_31p"] > 0).all()
    pd.testing.assert_frame_equal(daily[num], offline[num], check_dtype=False)


@pytest.mark.lesson2
def test_desc_len_from_first_seen_reaches_features():
    """Bug 2 regression: desc_len in the first-seen rows must not be replaced by 0."""
    world = _world(n=500)
    day = world["date_requested"].dt.normalize().value_counts().idxmax()
    new = world[world["date_requested"].dt.normalize() == day].assign(date_closed=pd.NaT)
    out = build_daily_features(prepare_requests(_state_on(world, day), "closed"),
                               prepare_requests(new, "open"), day)
    want = new.set_index("service_request_id")["desc_len"]
    got = out.set_index("service_request_id")["desc_len"]
    assert (got == want.loc[got.index]).all() and (got > 0).any()


def test_prepare_requests_computes_desc_len_from_text():
    """A frame with public_description but no desc_len (bronze-like) gets the length."""
    raw = _world(n=3).drop(columns="desc_len")
    raw["public_description"] = ["pothole", None, ""]
    assert prepare_requests(raw, "open")["desc_len"].tolist() == [7, 0, 0]
