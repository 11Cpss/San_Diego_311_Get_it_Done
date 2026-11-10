"""Checks the pandera data contract (lesson 4: you write `build_schema`).

Tests marked `lesson4` skip until `build_schema` is written;
`python scripts/lessons.py check 4` turns those skips into failures.
"""

import io

import pandas as pd
import pytest

from sd311.ingest import read_csv_as_strings, to_parquet_bytes
from sd311.schemas import REQUIRED_COLUMNS, validate


@pytest.fixture
def df(open_csv_bytes):
    return read_csv_as_strings(open_csv_bytes)


def test_fixture_has_all_required_columns(df):
    assert set(REQUIRED_COLUMNS) <= set(df.columns)
    assert len(df) == 1000


@pytest.mark.lesson4
def test_good_file_passes(df):
    assert validate(df) == []


@pytest.mark.lesson4
@pytest.mark.parametrize("col", ["status", "service_request_id", "date_closed"])
def test_missing_required_column_fails(df, col):
    problems = validate(df.drop(columns=[col]))
    assert problems and any(col in p for p in problems)


@pytest.mark.lesson4
def test_extra_and_missing_optional_columns_ok(df):
    df2 = df.drop(columns=["public_url", "iamfloc"]).assign(brand_new_column="x")
    assert validate(df2) == []


@pytest.mark.lesson4
def test_unparseable_date_fails(df):
    df.loc[3, "date_requested"] = "not a date"
    assert any("date_requested" in p for p in validate(df))


@pytest.mark.lesson4
def test_blank_id_fails(df):
    df.loc[0, "service_request_id"] = ""
    assert any("service_request_id" in p for p in validate(df))


@pytest.mark.lesson4
def test_blank_date_requested_fails(df):
    df.loc[7, "date_requested"] = ""
    assert any("date_requested" in p for p in validate(df))


@pytest.mark.lesson4
def test_blank_values_in_other_required_columns_are_ok(df):
    # Open requests have no date_closed; many rows have no district or plan name.
    df["date_closed"] = ""
    df.loc[:10, ["council_district", "comm_plan_name", "case_origin"]] = ""
    assert validate(df) == []


@pytest.mark.lesson4
def test_dropping_location_and_text_columns_is_ok(df):
    df2 = df.drop(columns=["lat", "lng", "public_description"]).assign(another_new="1")
    assert validate(df2) == []


@pytest.mark.lesson4
def test_every_problem_is_reported_at_once(df):
    """validate() runs the schema in lazy mode, so one run lists every broken rule."""
    df.loc[0, "service_request_id"] = ""
    df.loc[1, "date_requested"] = "yesterday-ish"
    problems = validate(df.drop(columns=["case_origin"]))
    text = " | ".join(problems)
    for col in ("service_request_id", "date_requested", "case_origin"):
        assert col in text, f"{col} missing from {problems}"


@pytest.mark.lesson4
def test_schema_lists_every_contract_column():
    from sd311.schemas import OPTIONAL_COLUMNS, build_schema

    schema = build_schema()
    assert set(REQUIRED_COLUMNS) | set(OPTIONAL_COLUMNS) <= set(schema.columns)
    assert all(schema.columns[c].required for c in REQUIRED_COLUMNS)
    assert not any(schema.columns[c].required for c in OPTIONAL_COLUMNS)
    assert schema.strict is False


def test_empty_file_fails(df):
    assert validate(df.head(0)) == ["file has no data rows"]


def test_parquet_is_all_strings_plus_lineage(df):
    out = pd.read_parquet(io.BytesIO(to_parquet_bytes(df, "2026-10-08", "open")))
    assert len(out) == len(df)
    assert (out["snapshot_date"] == "2026-10-08").all()
    assert (out["source"] == "open").all()
    assert all(pd.api.types.is_string_dtype(out[c]) for c in out.columns)
