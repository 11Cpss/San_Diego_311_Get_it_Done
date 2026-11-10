"""Data contract for the city's Get It Done CSV files (pandera).

We read every column as a *string* (dtype=str). That way a surprise value
like "N/A" in a numeric column never crashes the reader; typing happens
later in Databricks (silver layer). Here we only check what must be true
for the file to be usable at all.
"""

from __future__ import annotations

from functools import cache

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError, SchemaErrors

REQUIRED_COLUMNS = [
    "service_request_id",
    "date_requested",
    "case_age_days",
    "service_name",
    "status",
    "case_origin",
    "council_district",
    "comm_plan_name",
    "date_closed",
]

OPTIONAL_COLUMNS = [
    "service_request_parent_id",
    "sap_notification_number",
    "case_record_type",
    "service_name_detail",
    "lat",
    "lng",
    "street_address",
    "zipcode",
    "comm_plan_code",
    "park_name",
    "referred",
    "iamfloc",
    "floc",
    "public_description",
    "specify_the_issue",
    "public_url",
]


def _parses_as_datetime(s: pd.Series) -> pd.Series:
    """True where the value parses as a timestamp (element-wise check)."""
    return pd.to_datetime(s, errors="coerce", format="mixed").notna()


def _is_str(s: pd.Series) -> pd.Series:
    return s.map(lambda v: isinstance(v, str))


def build_schema() -> pa.DataFrameSchema:
    """The data contract: what every city CSV must satisfy to be loaded.

    LESSON 4: you write this. Return a ``pa.DataFrameSchema`` with these rules:

    * ``service_request_id``: must exist, no nulls, and no empty strings
      (hint: ``pa.Check.str_length(min_value=1)``).
    * ``date_requested``: must exist, no nulls, and every value must parse as a
      timestamp (hint: wrap ``_parses_as_datetime`` above in ``pa.Check(...)`` and
      give it an ``error=`` message that names the column).
    * ``status``: must exist and have no nulls.
    * Every other name in ``REQUIRED_COLUMNS``: must exist; blank or null values
      are fine (the open file has no ``date_closed``, for example).
    * Every name in ``OPTIONAL_COLUMNS``: checked only if present
      (``required=False``), nulls allowed.
    * Columns we have never heard of must NOT fail the file (the city adds
      columns now and then): think about ``strict``.
    * Don't convert types: every value is a string already (``coerce=False``).

    Every column holds strings, so the column type is ``str``.
    Check yourself: ``python scripts/lessons.py check 4``.
    """

    columns = {
        'status' : pa.Column(str, nullable = False),
        'service_request_id' : pa.Column(str, nullable = False, checks = pa.Check.str_length(min_value = 1)),
        "date_requested": pa.Column(
            str,
            nullable=False,
            checks=pa.Check(
                _parses_as_datetime,
                error="date_requested must be a valid date",
            ),
        ),
        
        
    }
    for name in REQUIRED_COLUMNS:
        columns.setdefault(name, pa.Column(str, nullable=True))

    for name in OPTIONAL_COLUMNS:
        columns[name] = pa.Column(str, nullable=True, required=False)
    return pa.DataFrameSchema(columns, strict = False, coerce = False)
    
    


@cache
def get_schema() -> pa.DataFrameSchema:
    """Build the schema once, on first use (not at import time)."""
    return build_schema()


def validate(df: pd.DataFrame) -> list[str]:
    """Return a list of human-readable problems (empty list == valid)."""
    if df.empty:
        return ["file has no data rows"]
    try:
        get_schema().validate(df, lazy=True)  # lazy=True collects *all* errors
    except SchemaErrors as err:
        fc = err.failure_cases
        msgs = []
        for _, row in fc.head(20).iterrows():
            col = row.get("column")
            col = col if isinstance(col, str) else "<dataframe>"
            msgs.append(f"{col}: {row.get('check')} "
                        f"(example: {row.get('failure_case')!r})")
        return msgs or [str(err)[:500]]
    except SchemaError as err:  # pragma: no cover - lazy mode normally raises SchemaErrors
        return [str(err)[:500]]
    return []
