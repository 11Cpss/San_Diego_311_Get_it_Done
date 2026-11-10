"""Loading the raw Get It Done files.

The raw data comes as one gzip CSV per calendar year of *closure*
(``..._closed_YYYY_...``) plus one snapshot of requests that were still
open when the snapshot was taken (``..._open_...``).

Everything is read as ``str`` first and then converted, so that IDs keep
leading zeros and nothing is silently mis-parsed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

# Where the slim CSVs live. Override with the env var SD311_DATA_DIR or the
# scripts' --data-dir flag. Default: <repo>/ml/data/raw (git-ignored).
_ML_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = Path(os.environ.get("SD311_DATA_DIR", _ML_DIR / "data" / "raw"))
CLOSED_YEARS = range(2016, 2026)

CATEGORICAL_COLS = [
    "case_record_type", "service_name", "service_name_detail", "status",
    "council_district", "comm_plan_code", "comm_plan_name", "case_origin",
    "referred", "park_name", "zipcode",
]


def _convert_types(df: pd.DataFrame) -> pd.DataFrame:
    """Convert the all-string frame to useful dtypes."""
    df = df.copy()
    df["date_requested"] = pd.to_datetime(df["date_requested"], errors="coerce")
    df["date_closed"] = pd.to_datetime(df["date_closed"], errors="coerce")  # date only
    df["case_age_days"] = pd.to_numeric(df["case_age_days"], errors="coerce")
    df["desc_len"] = pd.to_numeric(df["desc_len"], errors="coerce").fillna(0).astype("int32")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce").astype("float32")
    df["lng"] = pd.to_numeric(df["lng"], errors="coerce").astype("float32")
    return df


def load_closed(raw_dir: Path | str = DEFAULT_RAW_DIR, years=CLOSED_YEARS) -> pd.DataFrame:
    """Load the closed-request files for ``years`` and tag them source='closed'."""
    raw_dir = Path(raw_dir)
    parts = []
    for y in years:
        path = raw_dir / f"get_it_done_requests_closed_{y}_datasd.slim.csv.gz"
        part = pd.read_csv(path, dtype=str)
        part["source"] = "closed"
        parts.append(part)
    return _convert_types(pd.concat(parts, ignore_index=True))


def load_open(raw_dir: Path | str = DEFAULT_RAW_DIR) -> pd.DataFrame:
    """Load the snapshot of still-open requests and tag them source='open'."""
    path = Path(raw_dir) / "get_it_done_requests_open_datasd.slim.csv.gz"
    df = pd.read_csv(path, dtype=str)
    df["source"] = "open"
    return _convert_types(df)


def infer_extract_date(open_df: pd.DataFrame) -> pd.Timestamp:
    """Infer the day the open snapshot was extracted.

    For an open request, ``case_age_days`` is "days from the request date to
    the extract date", so ``date(date_requested) + case_age_days`` should be
    the same day for (almost) every row. We take the most common value.
    (On the files in this repo it is 2026-01-14 for ~98% of rows.)
    """
    implied = open_df["date_requested"].dt.normalize() + pd.to_timedelta(
        open_df["case_age_days"], unit="D")
    return pd.Timestamp(implied.mode().iloc[0])


def load_all(raw_dir: Path | str = DEFAULT_RAW_DIR) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Load closed + open rows into one frame (column ``source`` says which).

    Returns ``(df, extract_date)``. The frame is *not* cleaned yet; see
    :func:`sd311_core.cleaning.clean_requests`.
    """
    closed = load_closed(raw_dir)
    opened = load_open(raw_dir)
    extract_date = infer_extract_date(opened)
    df = pd.concat([closed, opened], ignore_index=True)
    return df, extract_date
