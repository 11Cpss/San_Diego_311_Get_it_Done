from datetime import date

from sd311.ingest import (
    build_manifest,
    is_stale,
    manifest_key,
    quarantine_key,
    raw_key,
    sha256_hex,
    source_files,
    volume_path,
)


def test_raw_key_layout():
    assert (raw_key("open", "2026-10-08", "get_it_done_requests_open_datasd.csv")
            == "raw/open/snapshot_date=2026-10-08/get_it_done_requests_open_datasd.csv")
    assert manifest_key("open", "2026-10-08") == "raw/open/snapshot_date=2026-10-08/manifest.json"
    assert quarantine_key("open", "2026-10-08", "f.csv") == \
        "quarantine/open/snapshot_date=2026-10-08/f.csv"


def test_volume_path_swaps_extension():
    p = volume_path("/Volumes/sd311/bronze/landing", "closed_2026", "2026-10-08",
                    "get_it_done_requests_closed_2026_datasd.csv")
    assert p == ("/Volumes/sd311/bronze/landing/closed_2026/snapshot_date=2026-10-08/"
                 "get_it_done_requests_closed_2026_datasd.parquet")


def test_source_files_normal_month():
    names = [f.filename for f in source_files(date(2026, 10, 8))]
    assert names == ["get_it_done_requests_open_datasd.csv",
                     "get_it_done_requests_closed_2026_datasd.csv"]


def test_source_files_january_includes_previous_year():
    sources = [f.source for f in source_files(date(2027, 1, 15))]
    assert sources == ["open", "closed_2027", "closed_2026"]


def test_manifest_fields_and_first_run_not_stale():
    m = build_manifest(url="u", data=b"abc", rows=1, headers={"ETag": "x", "Junk": "y"},
                       fetched_at="t", previous=None)
    assert m["sha256"] == sha256_hex(b"abc")
    assert m["bytes"] == 3 and m["rows"] == 1
    assert m["http_headers"] == {"ETag": "x"}
    assert m["previous_sha256"] is None
    assert is_stale(m) is False


def test_same_sha_is_stale_different_sha_is_not():
    prev = {"sha256": sha256_hex(b"abc"), "snapshot_date": "2026-10-07"}
    same = build_manifest(url="u", data=b"abc", rows=1, headers={}, fetched_at="t",
                          previous=prev)
    diff = build_manifest(url="u", data=b"abcd", rows=1, headers={}, fetched_at="t",
                          previous=prev)
    assert is_stale(same) is True
    assert is_stale(diff) is False
    assert same["previous_snapshot_date"] == "2026-10-07"
