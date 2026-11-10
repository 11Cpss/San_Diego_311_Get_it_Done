"""End-to-end ingest run with everything external faked.

* moto      -> fake S3, SSM and CloudWatch (in-memory AWS)
* responses -> fake HTTP for the city server and the Databricks REST API

Run just this file:  pytest tests/integration -v

The first three tests need your lesson 4 code (`build_schema`, `upload_file`,
`run_now`); they are marked `lesson4` and skip until it is written.
"""

import io
import json

import boto3
import pandas as pd
import pytest
import requests
import responses
from moto import mock_aws

from sd311 import ingest
from sd311.config import Settings

BUCKET = "sd311-test-bucket"
HOST = "https://dbc-test.cloud.databricks.com"
BASE = "https://seshat.datasd.org/get_it_done_reports/"
DATE = "2026-10-08"
OPEN_URL = BASE + "get_it_done_requests_open_datasd.csv"
CLOSED_URL = BASE + "get_it_done_requests_closed_2026_datasd.csv"


@pytest.fixture
def aws():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-west-2")
        s3.create_bucket(Bucket=BUCKET,
                         CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
        ssm = boto3.client("ssm", region_name="us-west-2")
        for name, value in [("host", HOST), ("token", "dapi-test"), ("daily_job_id", "42")]:
            ssm.put_parameter(Name=f"/sd311/databricks/{name}", Value=value,
                              Type="SecureString")
        cw = boto3.client("cloudwatch", region_name="us-west-2")
        yield s3, ssm, cw


def settings():
    return Settings(bucket=BUCKET, base_url=BASE, http_retries=0)


def run(aws, snapshot_date=DATE):
    s3, ssm, cw = aws
    return ingest.handler({"snapshot_date": snapshot_date}, None, settings=settings(),
                          s3=s3, ssm=ssm, cw=cw,
                          session=ingest.http_session(settings()))


def mock_city(rsps, open_bytes, closed_bytes):
    rsps.add(responses.GET, OPEN_URL, body=open_bytes, status=200,
             headers={"ETag": '"abc"', "Last-Modified": "Thu, 08 Oct 2026 05:00:00 GMT"})
    rsps.add(responses.GET, CLOSED_URL, body=closed_bytes, status=200)


def mock_databricks(rsps):
    rsps.add(responses.PUT,
             responses.matchers.re.compile(rf"{HOST}/api/2.0/fs/files/Volumes/.*"),
             status=204)
    rsps.add(responses.POST, f"{HOST}/api/2.1/jobs/run-now", json={"run_id": 777})


def metric_names(cw):
    return {m["MetricName"] for m in cw.list_metrics(Namespace="SD311")["Metrics"]}


@pytest.mark.lesson4
@responses.activate
def test_happy_path_lands_everything(aws, open_csv_bytes):
    s3, _, cw = aws
    closed = open_csv_bytes  # same layout is fine for the test
    mock_city(responses, open_csv_bytes, closed)
    mock_databricks(responses)

    out = run(aws)

    assert out["status"] == "ok"
    assert out["run_id"] == 777
    # Raw CSV + manifest in S3
    key = f"raw/open/snapshot_date={DATE}/get_it_done_requests_open_datasd.csv"
    assert s3.get_object(Bucket=BUCKET, Key=key)["Body"].read() == open_csv_bytes
    manifest = json.loads(s3.get_object(
        Bucket=BUCKET, Key=f"raw/open/snapshot_date={DATE}/manifest.json")["Body"].read())
    assert manifest["rows"] == 1000
    assert manifest["url"] == OPEN_URL
    assert manifest["http_headers"]["ETag"] == '"abc"'
    assert manifest["previous_sha256"] is None

    # Parquet PUT to the Volume path with the bearer token
    puts = [c for c in responses.calls if c.request.method == "PUT"]
    assert len(puts) == 2
    assert puts[0].request.url.startswith(
        f"{HOST}/api/2.0/fs/files/Volumes/sd311/bronze/landing/open/snapshot_date={DATE}/"
        "get_it_done_requests_open_datasd.parquet")
    assert puts[0].request.headers["Authorization"] == "Bearer dapi-test"
    df = pd.read_parquet(io.BytesIO(puts[0].request.body))
    assert len(df) == 1000 and {"snapshot_date", "source"} <= set(df.columns)

    # run-now got the job id from SSM
    post = [c for c in responses.calls if c.request.method == "POST"][0]
    assert json.loads(post.request.body) == {"job_id": 42}

    # User-Agent looks like a browser
    assert "Mozilla" in responses.calls[0].request.headers["User-Agent"]
    assert "StaleSource" in metric_names(cw)


@pytest.mark.lesson4
@responses.activate
def test_second_day_same_bytes_is_stale(aws, open_csv_bytes):
    s3, _, cw = aws
    mock_city(responses, open_csv_bytes, open_csv_bytes)
    mock_databricks(responses)
    run(aws, "2026-10-07")
    out = run(aws, "2026-10-08")

    assert all(r["stale"] for r in out["results"])
    m = json.loads(s3.get_object(
        Bucket=BUCKET, Key="raw/open/snapshot_date=2026-10-08/manifest.json")["Body"].read())
    assert m["previous_sha256"] == m["sha256"]
    assert m["previous_snapshot_date"] == "2026-10-07"


@pytest.mark.lesson4
@responses.activate
def test_bad_schema_is_quarantined_and_stops(aws, open_csv_bytes):
    s3, _, cw = aws
    bad = pd.read_csv(io.BytesIO(open_csv_bytes), dtype=str).drop(columns=["status"])
    mock_city(responses, bad.to_csv(index=False).encode(), open_csv_bytes)
    mock_databricks(responses)

    out = run(aws)

    assert out["status"] == "failed"
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert any(k.startswith("quarantine/open/") for k in keys)
    assert not any(k.startswith("raw/") for k in keys)
    assert "DataQualityFailures" in metric_names(cw)
    # Nothing sent to Databricks, closed file not even downloaded
    assert not [c for c in responses.calls if c.request.method in ("PUT", "POST")]
    assert len(responses.calls) == 1


@responses.activate
def test_city_server_error_raises(aws):
    responses.add(responses.GET, OPEN_URL, status=503)
    with pytest.raises(requests.RequestException):
        run(aws)


def test_placeholder_secrets_fail_fast(aws):
    _, ssm, _ = aws
    ssm.put_parameter(Name="/sd311/databricks/token", Value="CHANGE_ME",
                      Type="SecureString", Overwrite=True)
    with pytest.raises(RuntimeError, match="placeholder"):
        run(aws)
