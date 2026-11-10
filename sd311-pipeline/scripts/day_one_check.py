#!/usr/bin/env python3
"""Day-one smoke test: can this laptop talk to everything the pipeline needs?

Checks
  a) Files API: upload a tiny file to a Volume, read it back, compare bytes
  b) Jobs API: run-now a job (if DATABRICKS_JOB_ID is set) and poll its state
  c) City server: download the first bytes (header row) of the open file
  d) Prints manual steps for the catboost pip test in a serverless notebook

Usage
  export DATABRICKS_HOST=https://dbc-xxxx.cloud.databricks.com
  export DATABRICKS_TOKEN=dapi...
  export DATABRICKS_JOB_ID=123            # optional
  python scripts/day_one_check.py [--volume /Volumes/sd311/bronze/landing]

Exit code 0 only if every check that ran passed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

CATBOOST_INSTRUCTIONS = """\
Manual check (d): catboost on serverless
  1. In the Databricks workspace: New -> Notebook, attach to "Serverless".
  2. Cell 1:   %pip install catboost==1.2.7
  3. Cell 2:   dbutils.library.restartPython()
  4. Cell 3:   import catboost; print(catboost.__version__)
  5. PASS if the version prints. If pip is blocked, add catboost to the
     notebook's Environment panel (side bar -> Environment -> Dependencies).
"""


def check_files_api(dbx, volume: str) -> tuple[bool, str]:
    path = f"{volume}/_day_one_check/hello.txt"
    payload = f"sd311 day-one check {datetime.now(UTC).isoformat()}".encode()
    dbx.upload_file(path, payload)
    back = dbx.download_file(path)
    if back == payload:
        return True, f"wrote+read {len(payload)} bytes at {path}"
    return False, f"read-back mismatch at {path}: {back!r}"


def check_jobs_api(dbx, job_id: int, timeout_s: int) -> tuple[bool, str]:
    run_id = dbx.run_now(job_id)
    deadline = time.time() + timeout_s
    state = "?"
    while time.time() < deadline:
        run = dbx.get_run(run_id)
        # Newer API returns status.state; older returns state.life_cycle_state.
        st = run.get("status") or {}
        state = st.get("state") or run.get("state", {}).get("life_cycle_state", "?")
        if state in ("TERMINATED", "INTERNAL_ERROR", "SKIPPED"):
            result = (run.get("state", {}).get("result_state")
                      or (st.get("termination_details") or {}).get("code", ""))
            ok = result == "SUCCESS"
            return ok, f"run_id={run_id} state={state} result={result}"
        time.sleep(10)
    return False, f"run_id={run_id} still {state} after {timeout_s}s (check UI)"


def check_city(session, url: str, timeout: int) -> tuple[bool, str]:
    r = session.get(url, headers={"Range": "bytes=0-2047"}, timeout=timeout, stream=True)
    r.raise_for_status()
    first = next(r.iter_content(2048), b"").decode("utf-8", "replace").splitlines()[0]
    ok = "service_request_id" in first
    return ok, f"HTTP {r.status_code}, header starts: {first[:80]}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--volume", default="/Volumes/sd311/bronze/landing",
                    help="Volume directory to write the test file to")
    ap.add_argument("--job-timeout", type=int, default=600,
                    help="seconds to wait for the job run (default 600)")
    ap.add_argument("--skip-city", action="store_true", help="skip check (c)")
    args = ap.parse_args(argv)

    # Imports after argparse so `--help` works even without dependencies.
    from sd311.config import DEFAULT_BASE_URL, Settings
    from sd311.ingest import DatabricksClient, http_session

    session = http_session(Settings(bucket="unused"))
    host, token = os.environ.get("DATABRICKS_HOST"), os.environ.get("DATABRICKS_TOKEN")
    job_id = os.environ.get("DATABRICKS_JOB_ID")
    results: list[tuple[str, str, str]] = []

    def run(name, fn):
        try:
            ok, detail = fn()
            results.append((name, "PASS" if ok else "FAIL", detail))
        except Exception as exc:  # noqa: BLE001 - report every failure in the table
            results.append((name, "FAIL", repr(exc)[:200]))

    if host and token:
        dbx = DatabricksClient(host, token, session)
        run("a) Files API upload+read", lambda: check_files_api(dbx, args.volume))
        if job_id:
            run("b) Jobs run-now + poll", lambda: check_jobs_api(dbx, int(job_id),
                                                                  args.job_timeout))
        else:
            results.append(("b) Jobs run-now + poll", "SKIP", "DATABRICKS_JOB_ID not set"))
    else:
        for name in ("a) Files API upload+read", "b) Jobs run-now + poll"):
            results.append((name, "SKIP", "DATABRICKS_HOST/DATABRICKS_TOKEN not set"))

    if args.skip_city:
        results.append(("c) City open-file header", "SKIP", "--skip-city"))
    else:
        url = DEFAULT_BASE_URL + "get_it_done_requests_open_datasd.csv"
        run("c) City open-file header", lambda: check_city(session, url, 30))

    results.append(("d) catboost on serverless", "MANUAL", "see instructions below"))

    width = max(len(r[0]) for r in results)
    print(f"\n{'CHECK'.ljust(width)}  RESULT  DETAIL")
    for name, status, detail in results:
        print(f"{name.ljust(width)}  {status.ljust(6)}  {detail}")
    print()
    print(CATBOOST_INSTRUCTIONS)
    return 1 if any(r[1] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
