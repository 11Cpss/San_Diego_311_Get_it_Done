"""The AWS -> Databricks hand-off (lesson 4: you write `upload_file` and `run_now`).

The fake Databricks server (`responses`) only answers requests that match the
real API contract exactly, so these tests fail if the URL, method, query
parameter, headers or body are wrong.
"""

import json

import pytest
import requests
import responses

from sd311.ingest import DatabricksClient

pytestmark = pytest.mark.lesson4

HOST = "https://dbc-test.cloud.databricks.com"
VOLUME_FILE = "/Volumes/sd311/bronze/landing/open/snapshot_date=2026-10-08/open.parquet"
FILES_URL = f"{HOST}/api/2.0/fs/files{VOLUME_FILE}"
RUN_NOW_URL = f"{HOST}/api/2.1/jobs/run-now"


def client(host=HOST):
    return DatabricksClient(host, "dapi-secret", requests.Session(), timeout=5)


@responses.activate
def test_upload_puts_raw_bytes_to_the_volume_path():
    responses.add(responses.PUT, FILES_URL, status=204,
                  match=[responses.matchers.query_param_matcher({"overwrite": "true"})])
    client().upload_file(VOLUME_FILE, b"PAR1\x00\x01binary")

    (call,) = responses.calls
    assert call.request.method == "PUT"
    assert call.request.body == b"PAR1\x00\x01binary"          # raw bytes, not JSON
    assert call.request.headers["Authorization"] == "Bearer dapi-secret"
    assert call.request.headers["Content-Type"] == "application/octet-stream"


@responses.activate
def test_upload_can_refuse_to_overwrite():
    responses.add(responses.PUT, FILES_URL, status=204,
                  match=[responses.matchers.query_param_matcher({"overwrite": "false"})])
    client().upload_file(VOLUME_FILE, b"x", overwrite=False)
    assert len(responses.calls) == 1


@responses.activate
def test_upload_adds_https_to_a_bare_host():
    responses.add(responses.PUT, FILES_URL, status=204)
    client(host="dbc-test.cloud.databricks.com/").upload_file(VOLUME_FILE, b"x")
    assert responses.calls[0].request.url.startswith(FILES_URL)


@responses.activate
def test_upload_error_raises():
    responses.add(responses.PUT, FILES_URL, status=403, json={"error_code": "PERMISSION_DENIED"})
    with pytest.raises(requests.HTTPError):
        client().upload_file(VOLUME_FILE, b"x")


@responses.activate
def test_run_now_posts_job_id_as_int_and_returns_run_id():
    responses.add(responses.POST, RUN_NOW_URL, json={"run_id": 777, "number_in_job": 1})
    run_id = client().run_now("42")          # SSM values are strings

    assert run_id == 777 and isinstance(run_id, int)
    (call,) = responses.calls
    assert json.loads(call.request.body) == {"job_id": 42}   # no job_parameters by default
    assert call.request.headers["Authorization"] == "Bearer dapi-secret"


@responses.activate
def test_run_now_passes_job_parameters_when_given():
    responses.add(responses.POST, RUN_NOW_URL, json={"run_id": "778"})
    assert client().run_now(42, job_parameters={"snapshot_date": "2026-10-08"}) == 778
    body = json.loads(responses.calls[0].request.body)
    assert body == {"job_id": 42, "job_parameters": {"snapshot_date": "2026-10-08"}}


@responses.activate
def test_run_now_error_raises():
    responses.add(responses.POST, RUN_NOW_URL, status=400,
                  json={"error_code": "INVALID_PARAMETER_VALUE"})
    with pytest.raises(requests.HTTPError):
        client().run_now(42)
