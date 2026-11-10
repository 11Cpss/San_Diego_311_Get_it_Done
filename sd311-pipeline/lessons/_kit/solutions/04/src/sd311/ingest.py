"""Lambda `ingest`: city CSV -> validate -> S3 raw -> Parquet -> Databricks.

Flow for each source file (see README "Architecture"):

    download --> sha256 + rows --> pandera validate
        |  fail: quarantine/ in S3, DataQualityFailures metric, STOP
        v  ok
    raw/<source>/snapshot_date=YYYY-MM-DD/<file>.csv  + manifest.json
    StaleSource metric (sha == previous snapshot's sha?)
    Parquet (all strings + snapshot_date + source)
    PUT Files API -> /Volumes/sd311/bronze/landing/<source>/snapshot_date=.../<file>.parquet

When every file landed: POST /api/2.1/jobs/run-now {job_id}, log run_id.

Everything that talks to the network is in a small function so tests can
swap it out (moto for AWS, `responses` for HTTP).
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import boto3
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from sd311.config import Settings, load_settings
from sd311.logging_utils import log_step, timed
from sd311.schemas import validate

# --------------------------------------------------------------------------
# 1. Pure helpers (no network) -- these are what the unit tests cover.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFile:
    source: str    # short logical name, e.g. "open" or "closed_2026"
    filename: str  # file name on the city server


def source_files(today: date) -> list[SourceFile]:
    """Which files to fetch today.

    The open file always; the closed file for the current year; and in
    January also last year's closed file (cases closed late in December
    keep trickling into it for a few weeks).
    """
    files = [SourceFile("open", "get_it_done_requests_open_datasd.csv")]
    years = [today.year] + ([today.year - 1] if today.month == 1 else [])
    for y in years:
        files.append(SourceFile(f"closed_{y}", f"get_it_done_requests_closed_{y}_datasd.csv"))
    return files


def snapshot_prefix(source: str, snapshot_date: str) -> str:
    return f"raw/{source}/snapshot_date={snapshot_date}/"


def raw_key(source: str, snapshot_date: str, filename: str) -> str:
    """s3 key for the untouched CSV, e.g. raw/open/snapshot_date=2026-10-08/x.csv"""
    return snapshot_prefix(source, snapshot_date) + filename


def manifest_key(source: str, snapshot_date: str) -> str:
    return snapshot_prefix(source, snapshot_date) + "manifest.json"


def quarantine_key(source: str, snapshot_date: str, filename: str) -> str:
    return f"quarantine/{source}/snapshot_date={snapshot_date}/{filename}"


def parquet_name(filename: str) -> str:
    return filename.rsplit(".", 1)[0] + ".parquet"


def volume_path(volume_root: str, source: str, snapshot_date: str, filename: str) -> str:
    """Unity Catalog Volume path the Databricks job reads from."""
    return f"{volume_root}/{source}/snapshot_date={snapshot_date}/{parquet_name(filename)}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(*, url: str, data: bytes, rows: int, headers: dict, fetched_at: str,
                   previous: dict | None) -> dict:
    """The manifest is the 'receipt' for one download. Keep it boring and explicit."""
    keep = ["Content-Type", "Content-Length", "Last-Modified", "ETag", "Date"]
    return {
        "manifest_version": 1,
        "url": url,
        "sha256": sha256_hex(data),
        "rows": rows,
        "bytes": len(data),
        "http_headers": {k: headers[k] for k in keep if k in headers},
        "fetched_at": fetched_at,
        "previous_sha256": previous.get("sha256") if previous else None,
        "previous_snapshot_date": previous.get("snapshot_date") if previous else None,
    }


def is_stale(manifest: dict) -> bool:
    """Stale == byte-identical to the previous snapshot (city did not refresh)."""
    prev = manifest.get("previous_sha256")
    return prev is not None and prev == manifest["sha256"]


def read_csv_as_strings(data: bytes) -> pd.DataFrame:
    """Every column as string; blanks stay '' instead of becoming NaN."""
    return pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)


def to_parquet_bytes(df: pd.DataFrame, snapshot_date: str, source: str) -> bytes:
    out = df.astype(str).copy()
    out["snapshot_date"] = snapshot_date
    out["source"] = source
    buf = io.BytesIO()
    out.to_parquet(buf, index=False, engine="pyarrow")
    return buf.getvalue()


def today_local(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


# --------------------------------------------------------------------------
# 2. Network clients
# --------------------------------------------------------------------------


def http_session(settings: Settings) -> requests.Session:
    """requests.Session with retries + exponential backoff on 429/5xx."""
    s = requests.Session()
    retry = Retry(total=settings.http_retries, backoff_factor=2,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET", "HEAD", "PUT", "POST"))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = settings.user_agent
    return s


def download(session: requests.Session, url: str, timeout: int) -> tuple[bytes, dict]:
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content, dict(resp.headers)


class DatabricksClient:
    """Tiny client for the two REST APIs we need (no SDK = smaller image).

    Files API:  PUT/GET /api/2.0/fs/files{/Volumes/...}   (raw bytes body)
    Jobs API:   POST /api/2.1/jobs/run-now {"job_id": N}  -> {"run_id": ...}
                GET  /api/2.1/jobs/runs/get?run_id=N
    """

    def __init__(self, host: str, token: str, session: requests.Session, timeout: int = 60):
        self.host = host.rstrip("/")
        if not self.host.startswith("http"):
            self.host = "https://" + self.host
        self.session = session
        self.timeout = timeout
        self.auth = {"Authorization": f"Bearer {token}"}

    def upload_file(self, path: str, data: bytes, overwrite: bool = True) -> None:
        r = self.session.put(f"{self.host}/api/2.0/fs/files{path}",
                             params={"overwrite": str(overwrite).lower()},
                             headers={**self.auth, "Content-Type": "application/octet-stream"},
                             data=data, timeout=self.timeout)
        r.raise_for_status()

    def download_file(self, path: str) -> bytes | None:
        """Return file bytes, or None if the file does not exist (404)."""
        r = self.session.get(f"{self.host}/api/2.0/fs/files{path}",
                             headers=self.auth, timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def run_now(self, job_id: int, job_parameters: dict | None = None) -> int:
        body: dict = {"job_id": int(job_id)}
        if job_parameters:
            body["job_parameters"] = job_parameters
        r = self.session.post(f"{self.host}/api/2.1/jobs/run-now", headers=self.auth,
                              json=body, timeout=self.timeout)
        r.raise_for_status()
        return int(r.json()["run_id"])

    def get_run(self, run_id: int) -> dict:
        r = self.session.get(f"{self.host}/api/2.1/jobs/runs/get", headers=self.auth,
                             params={"run_id": run_id}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


def get_databricks_secrets(ssm, prefix: str) -> dict:
    """Read host, token, daily_job_id SecureStrings from SSM in one call."""
    names = [f"{prefix}/host", f"{prefix}/token", f"{prefix}/daily_job_id"]
    resp = ssm.get_parameters(Names=names, WithDecryption=True)
    found = {p["Name"].rsplit("/", 1)[1]: p["Value"] for p in resp["Parameters"]}
    missing = [n for n in names if n.rsplit("/", 1)[1] not in found]
    if missing:
        raise RuntimeError(f"missing SSM parameters: {missing}")
    placeholders = [k for k, v in found.items() if v == "CHANGE_ME"]
    if placeholders:
        # Terraform creates the parameters with this placeholder; see README "Deploy".
        raise RuntimeError(f"SSM parameters still have placeholder values: {placeholders}")
    return found


def put_metric(cw, namespace: str, name: str, value: float, unit: str = "Count",
               dimensions: dict | None = None) -> None:
    datum = {"MetricName": name, "Value": float(value), "Unit": unit}
    if dimensions:
        datum["Dimensions"] = [{"Name": k, "Value": v} for k, v in dimensions.items()]
    cw.put_metric_data(Namespace=namespace, MetricData=[datum])
    log_step("metric", metric=name, value=value, dimensions=dimensions or {})


def previous_manifest(s3, bucket: str, source: str, snapshot_date: str) -> dict | None:
    """Most recent manifest strictly *before* snapshot_date, or None.

    Keys sort lexically by date because the format is YYYY-MM-DD.
    """
    prefix = f"raw/{source}/snapshot_date="
    candidates = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/manifest.json"):
                d = key[len(prefix):].split("/", 1)[0]
                if d < snapshot_date:
                    candidates.append((d, key))
    if not candidates:
        return None
    d, key = max(candidates)
    body = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    body["snapshot_date"] = d
    return body


# --------------------------------------------------------------------------
# 3. Orchestration
# --------------------------------------------------------------------------


def process_source(sf: SourceFile, *, settings: Settings, snapshot_date: str, session,
                   s3, cw, dbx: DatabricksClient | None) -> dict:
    """Run the full pipeline for one file. Returns a small result dict."""
    url = settings.base_url.rstrip("/") + "/" + sf.filename
    log_ctx = {"source": sf.source, "snapshot_date": snapshot_date}

    with timed("download", url=url, **log_ctx) as extra:
        data, headers = download(session, url, settings.http_timeout_s)
        extra["bytes"] = len(data)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")

    # Parse + validate. A file that will not even parse is also a DQ failure.
    # Only the parse is inside the try: a bug in validate() is a code error and
    # must surface as one, not be filed as "bad data" in quarantine.
    try:
        df = read_csv_as_strings(data)
    except Exception as exc:  # noqa: BLE001 - any parse error means "bad file"
        df, problems = None, [f"could not parse CSV: {exc!r}"]
    else:
        problems = validate(df)
    rows = 0 if df is None else len(df)
    log_step("validate", ok=not problems, rows=rows, sha256=sha256_hex(data),
             problems=problems[:5], **log_ctx)

    if problems:
        qkey = quarantine_key(sf.source, snapshot_date, sf.filename)
        s3.put_object(Bucket=settings.bucket, Key=qkey, Body=data)
        s3.put_object(Bucket=settings.bucket, Key=qkey + ".errors.json",
                      Body=json.dumps({"url": url, "problems": problems}, indent=2).encode())
        log_step("quarantine", level="ERROR", key=qkey, **log_ctx)
        put_metric(cw, settings.metric_namespace, "DataQualityFailures", 1)
        return {"source": sf.source, "status": "quarantined", "key": qkey,
                "problems": problems}

    prev = previous_manifest(s3, settings.bucket, sf.source, snapshot_date)
    manifest = build_manifest(url=url, data=data, rows=rows, headers=headers,
                              fetched_at=fetched_at, previous=prev)
    rkey = raw_key(sf.source, snapshot_date, sf.filename)
    s3.put_object(Bucket=settings.bucket, Key=rkey, Body=data)
    s3.put_object(Bucket=settings.bucket, Key=manifest_key(sf.source, snapshot_date),
                  Body=json.dumps(manifest, indent=2).encode(),
                  ContentType="application/json")
    log_step("s3_raw", key=rkey, rows=rows, **log_ctx)

    stale = is_stale(manifest)
    put_metric(cw, settings.metric_namespace, "StaleSource", 1 if stale else 0,
               dimensions={"Source": sf.source})

    vpath = None
    if dbx is not None:
        with timed("databricks_upload", **log_ctx) as extra:
            pq = to_parquet_bytes(df, snapshot_date, sf.source)
            vpath = volume_path(settings.volume_root, sf.source, snapshot_date, sf.filename)
            dbx.upload_file(vpath, pq)
            extra.update(path=vpath, parquet_bytes=len(pq))

    return {"source": sf.source, "status": "landed", "rows": rows, "stale": stale,
            "raw_key": rkey, "volume_path": vpath}


def handler(event, context, *, settings: Settings | None = None, session=None,
            s3=None, ssm=None, cw=None) -> dict:
    """Lambda entry point.

    Optional event keys (for manual reruns / tests):
      {"snapshot_date": "2026-10-08", "trigger_job": false}
    """
    settings = settings or load_settings()
    s3 = s3 or boto3.client("s3")
    ssm = ssm or boto3.client("ssm")
    cw = cw or boto3.client("cloudwatch")
    session = session or http_session(settings)
    event = event or {}

    snapshot_date = event.get("snapshot_date") or today_local(settings.timezone).isoformat()
    files = source_files(date.fromisoformat(snapshot_date))
    log_step("start", snapshot_date=snapshot_date, files=[f.filename for f in files])

    dbx, job_id = None, None
    if settings.databricks_enabled:
        secrets = get_databricks_secrets(ssm, settings.ssm_prefix)
        dbx = DatabricksClient(secrets["host"], secrets["token"], session,
                               timeout=settings.http_timeout_s)
        job_id = secrets["daily_job_id"]

    results = []
    for sf in files:
        res = process_source(sf, settings=settings, snapshot_date=snapshot_date,
                             session=session, s3=s3, cw=cw, dbx=dbx)
        results.append(res)
        if res["status"] != "landed":
            # Stop: do not trigger Databricks on partial / bad data.
            log_step("stop", level="ERROR", reason="validation_failed", **res)
            return {"status": "failed", "snapshot_date": snapshot_date, "results": results}

    run_id = None
    if dbx is not None and event.get("trigger_job", True):
        # We don't pass job_parameters: the job derives the date itself, and
        # sending parameters a job doesn't define can be rejected by the API.
        run_id = dbx.run_now(int(job_id))
        log_step("databricks_run_now", job_id=job_id, run_id=run_id)

    out = {"status": "ok", "snapshot_date": snapshot_date, "results": results,
           "run_id": run_id}
    log_step("done", status="ok", run_id=run_id,
             rows=sum(r.get("rows", 0) for r in results))
    return out
