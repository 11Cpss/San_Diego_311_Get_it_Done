"""Cleaning rules, written once and applied to closed + open rows together.

Differences from the original notebooks (on purpose):

* Rules are applied to the UNION of closed and open requests. The old code
  only cleaned closed rows, so slow (still open) requests vanished.
* Duplicated ``service_request_id`` values keep the *closed* record, because
  it has the outcome (date_closed).
* The old ``case_age_days > 730`` filter is NOT applied. It used the outcome
  (how long the case took) to decide which rows to keep, which removes
  exactly the slow cases we need to predict.
"""
from __future__ import annotations

import pandas as pd


def dedupe_prefer_closed(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per service_request_id, preferring source=='closed'."""
    order = (df["source"] != "closed").astype(int)  # closed -> 0 sorts first
    df = df.assign(_order=order).sort_values(["service_request_id", "_order"], kind="stable")
    df = df.drop_duplicates("service_request_id", keep="first")
    return df.drop(columns="_order")


def clean_requests(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the cleaning rules and return a new, cleaned frame.

    Rules (in order):
      1. dedupe service_request_id, preferring the closed record
      2. drop status == "Referred" (handed to another agency; no city closure)
      3. drop service_name == "Other"
      4. drop case_age_days < 0, and closed rows whose date_closed is before
         the request date (both are data errors)
      5. any service_name containing "graffiti" (any case) -> "Graffiti"
      6. comm_plan_name title-cased; missing comm_plan_name / case_origin -> "Unknown"
      7. council_district missing -> "0"
    """
    df = dedupe_prefer_closed(df)
    df = df[df["status"] != "Referred"]
    df = df[df["service_name"] != "Other"]
    df = df[df["date_requested"].notna() & df["service_name"].notna()]
    df = df[~(df["case_age_days"] < 0)]
    bad_close = df["date_closed"].notna() & (df["date_closed"] < df["date_requested"].dt.normalize())
    df = df[~bad_close].copy()

    is_graffiti = df["service_name"].str.contains("graffiti", case=False, na=False)
    df.loc[is_graffiti, "service_name"] = "Graffiti"
    df["comm_plan_name"] = df["comm_plan_name"].str.title().fillna("Unknown")
    df["case_origin"] = df["case_origin"].fillna("Unknown")
    df["case_record_type"] = df["case_record_type"].fillna("Unknown")
    # "3" and "3.0" style values both appear in some extracts; normalise to int text.
    cd = pd.to_numeric(df["council_district"], errors="coerce").fillna(0).astype(int)
    df["council_district"] = cd.astype(str)

    df = df.sort_values("date_requested", kind="stable").reset_index(drop=True)
    return df
