"""Daily feature building and scoring for the Databricks job (pandas in, pandas out).

The Databricks tasks (databricks/tasks/features.py, score.py) are thin: they read
a *bounded window* of rows with Spark, call ``.toPandas()`` and hand the frames to
the functions here. Everything in this module is plain pandas, so it is unit
tested locally (ml/tests/test_serving.py).

Why a bounded window is enough for one scoring day t (= the snapshot date):

* workload_30d_*   needs requests submitted in [t-30d, t)
* mix90_*          needs labels that became final in (t-90d, t]. A label becomes
                   final when the request closes (+1 day), or - for a "31+" request,
                   open OR closed late - when it turns 31 days old (labels.py). So we need requests closed in the last
                   ~91 days, plus requests submitted in the last ~121 days.
* backlog_*        needs every request still open at t. The backlog formula is
                   "submitted before t" minus "closed before date(t)", so a request
                   that is in the window but already closed adds +1 and -1 = 0.
                   Any superset of "open at t" therefore gives the exact count.

``history_mask`` / ``history_filter_sql`` express exactly that filter.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .cleaning import clean_requests
from .features import CATEGORICAL_FEATURES, FEATURES, build_features
from .labels import BUCKETS

# Days of history the window needs (see module docstring). A few days of slack.
REQUESTED_LOOKBACK_DAYS = 125   # 90-day mix window + 31-day "open and old" rule + slack
CLOSED_LOOKBACK_DAYS = 95       # labels finalised by a closure in the mix window

# Column names in sd311.ml.predictions for the four probabilities.
PROBA_COLUMNS = {"0-2": "p_0_2", "3-7": "p_3_7", "8-30": "p_8_30", "31+": "p_31_plus"}
PREDICTION_COLUMNS = (["service_request_id", "snapshot_date", "model_version", "pred_bucket"]
                      + list(PROBA_COLUMNS.values()))

_NEEDED = ["service_request_id", "service_request_parent_id", "date_requested", "date_closed",
           "case_age_days", "case_record_type", "service_name", "status", "council_district",
           "comm_plan_name", "case_origin", "desc_len"]


def history_filter_sql(snapshot_date: str) -> str:
    """SQL WHERE clause (Spark SQL) selecting the bounded history for ``snapshot_date``."""
    d = pd.Timestamp(snapshot_date).date().isoformat()
    return (f"(CAST(date_requested AS DATE) >= DATE_SUB(DATE '{d}', {REQUESTED_LOOKBACK_DAYS})"
            f" OR date_closed IS NULL"
            f" OR CAST(date_closed AS DATE) >= DATE_SUB(DATE '{d}', {CLOSED_LOOKBACK_DAYS}))")


def history_mask(df: pd.DataFrame, snapshot_date) -> pd.Series:
    """Pandas twin of :func:`history_filter_sql` (used by the tests)."""
    t = pd.Timestamp(snapshot_date).normalize()
    return ((df["date_requested"] >= t - pd.Timedelta(days=REQUESTED_LOOKBACK_DAYS))
            | df["date_closed"].isna()
            | (df["date_closed"] >= t - pd.Timedelta(days=CLOSED_LOOKBACK_DAYS)))


def prepare_requests(pdf: pd.DataFrame, source: str) -> pd.DataFrame:
    """Make a Spark-exported frame look like sd311_core.io output.

    Silver/dbt tables keep some columns as strings or other types; this converts
    them and adds ``source`` (needed by clean_requests).

    ``desc_len`` (length of public_description, 0 if missing) is computed where the
    data enters silver (databricks/tasks/snapshots.py, dbt requests_clean), so it
    is normally a column already. A bronze-like frame with only the text gets the
    length computed here. A frame with neither gets 0 and a warning: that was the
    old train/serve skew (every scored request looked like "no description").
    """
    df = pdf.copy()
    if "desc_len" not in df.columns:
        if "public_description" in df.columns:
            df["desc_len"] = df["public_description"].fillna("").astype(str).str.len()
        else:
            warnings.warn("prepare_requests: no desc_len or public_description column; "
                          "using desc_len = 0 (features will not match training)", stacklevel=2)
    for c in _NEEDED:
        if c not in df.columns:
            df[c] = 0 if c == "desc_len" else np.nan
    df["service_request_id"] = df["service_request_id"].astype(str)
    df["date_requested"] = pd.to_datetime(df["date_requested"], errors="coerce")
    df["date_closed"] = pd.to_datetime(df["date_closed"], errors="coerce").dt.normalize()
    df["case_age_days"] = pd.to_numeric(df["case_age_days"], errors="coerce")
    df["desc_len"] = pd.to_numeric(df["desc_len"], errors="coerce").fillna(0).astype("int32")
    df["service_request_parent_id"] = df["service_request_parent_id"].where(
        df["service_request_parent_id"].notna() & (df["service_request_parent_id"].astype(str) != ""))
    for c in ["service_name", "status", "comm_plan_name", "case_origin", "case_record_type",
              "council_district"]:
        df[c] = df[c].astype("object").where(df[c].notna(), None)
    df["source"] = source
    return df


def build_daily_features(history: pd.DataFrame, new_rows: pd.DataFrame, snapshot_date) -> pd.DataFrame:
    """Features for ``new_rows`` (requests first seen on ``snapshot_date``).

    ``history`` = cleaned-table rows inside the bounded window (latest state),
    ``new_rows`` = the same requests' fields *as first seen*. Both go through
    :func:`prepare_requests` first. New rows replace their history version, so a
    request's own later edits can't leak into its features.

    Returns one row per kept new request: service_request_id, snapshot_date, FEATURES.
    """
    snap = pd.Timestamp(snapshot_date).normalize()
    new_ids = set(new_rows["service_request_id"].astype(str))
    hist = history[~history["service_request_id"].astype(str).isin(new_ids)]
    both = pd.concat([hist.assign(_is_new=False), new_rows.assign(_is_new=True)], ignore_index=True)
    cleaned = clean_requests(both)           # same rules as training
    if cleaned.empty or not cleaned["_is_new"].any():
        return pd.DataFrame(columns=["service_request_id", "snapshot_date"] + FEATURES)
    # as_of = end of the snapshot day: only labels final by then feed mix90_*.
    feats = build_features(cleaned, as_of=snap)
    keep = cleaned["_is_new"].to_numpy(dtype=bool)
    out = feats[keep].copy()
    out.insert(0, "snapshot_date", snap.date())
    out.insert(0, "service_request_id", cleaned.loc[keep, "service_request_id"].to_numpy())
    return out.reset_index(drop=True)


def features_for_storage(feats: pd.DataFrame) -> pd.DataFrame:
    """Categoricals -> plain strings so Spark can write the frame to Delta."""
    out = feats.copy()
    for c in CATEGORICAL_FEATURES:
        if c in out.columns:
            out[c] = out[c].astype(str)
    return out


def score_frame(model, feats: pd.DataFrame, snapshot_date, model_version: str) -> pd.DataFrame:
    """Predict buckets for a features frame; return the sd311.ml.predictions rows."""
    from .train import predict  # imported here so the module loads without catboost

    if feats.empty:
        return pd.DataFrame(columns=PREDICTION_COLUMNS)
    pred, proba = predict(model, feats)
    out = pd.DataFrame({
        "service_request_id": feats["service_request_id"].astype(str).to_numpy(),
        "snapshot_date": pd.Timestamp(snapshot_date).date(),
        "model_version": str(model_version),
        "pred_bucket": pred,
    })
    for i, b in enumerate(BUCKETS):
        out[PROBA_COLUMNS[b]] = proba[:, i].astype("float64")
    return out[PREDICTION_COLUMNS]
