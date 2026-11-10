"""Light tests for the helper scripts (no credentials, no network)."""

from pathlib import Path

import pytest
import responses

from scripts import backfill, day_one_check
from sd311.config import Settings
from sd311.ingest import DatabricksClient, http_session

HOST = "https://dbc-test.cloud.databricks.com"


def test_backfill_plan_paths():
    steps = backfill.plan([Path("/x/get_it_done_requests_closed_2019_datasd.slim.csv.gz")],
                          "/Volumes/sd311/bronze/landing", "2026-10-08")
    assert steps[0]["source"] == "closed_2019"
    assert steps[0]["s3_key"] == "backfill/get_it_done_requests_closed_2019_datasd.slim.csv.gz"
    assert steps[0]["volume_path"] == (
        "/Volumes/sd311/bronze/landing/backfill/snapshot_date=2026-10-08/"
        "get_it_done_requests_closed_2019_datasd.slim.parquet")


def test_backfill_dry_run_uploads_nothing(tmp_path, capsys):
    (tmp_path / "get_it_done_requests_closed_2020_datasd.csv").write_text("a\n1\n")
    assert backfill.main(["--dir", str(tmp_path), "--bucket", "b", "--dry-run"]) == 0
    assert "1 file(s) would be uploaded" in capsys.readouterr().out


@pytest.mark.lesson4  # uses DatabricksClient.upload_file
@responses.activate
def test_day_one_files_api_roundtrip():
    stored = {}

    def put(request):
        stored["body"] = request.body
        return (204, {}, "")

    def get(request):
        return (200, {}, stored["body"])

    url = f"{HOST}/api/2.0/fs/files/Volumes/v/_day_one_check/hello.txt"
    responses.add_callback(responses.PUT, url, callback=put)
    responses.add_callback(responses.GET, url, callback=get)
    dbx = DatabricksClient(HOST, "t", http_session(Settings(bucket="x", http_retries=0)))
    ok, detail = day_one_check.check_files_api(dbx, "/Volumes/v")
    assert ok, detail
