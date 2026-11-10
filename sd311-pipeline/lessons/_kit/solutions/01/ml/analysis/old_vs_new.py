"""Lesson 1 evidence: why the v1 headline numbers were misleading.

Steps
1. Rebuild the v1 test set (closed files only, 2023+, v1 cleaning with the
   outcome-based case_age_days <= 730 filter; test = requested >= 2025-07-01)
   and reproduce the v1 baselines: global train median, per-service median.
2. Count how many H2-2025 requests that test set silently dropped.
3. Compare the bucket distribution of the truncated test set with properly
   labeled data (labels as-of 2025-12-31, open cases kept).
   Steps 2 and 3 are `truncation_evidence` (you write it in lesson 1).
4. Estimate the size of the "January gap" (closed 2026-01-01..extract, in
   neither file) using the same window one year earlier.

Usage:  python analysis/old_vs_new.py            (needs data/dataset.parquet and the raw CSVs)
        python analysis/old_vs_new.py --verify   (recompute and compare with reports/old_vs_new.json;
                                                  writes nothing; skips if the data is missing)
Writes: reports/old_vs_new.md, reports/old_vs_new.json
"""
import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sd311_core.io import DEFAULT_RAW_DIR, load_closed  # noqa: E402
from sd311_core.labels import BUCKETS, days_to_bucket  # noqa: E402, F401  (days_to_bucket: lesson 1)

DATASET = ROOT / "data" / "dataset.parquet"
REPORT_JSON = ROOT / "reports" / "old_vs_new.json"

# v1's test period was "requested on or after 2025-07-01". All windows are half-open [start, end).
H2_START = pd.Timestamp("2025-07-01")   # second half of 2025 ...
H2_END = pd.Timestamp("2026-01-01")     # ... up to (not including) 2026-01-01
Q3_END = pd.Timestamp("2025-10-01")     # Q3-2025 = [H2_START, Q3_END): every label final at 2025-12-31


def v1_dataset(raw_dir=None) -> pd.DataFrame:
    """The v1 notebook pipeline, reproduced (closed files only)."""
    c = load_closed(raw_dir or DEFAULT_RAW_DIR, years=range(2023, 2026))
    c = c[(c["status"] != "Referred") & (c["service_name"] != "Other") & ~(c["case_age_days"] < 0)]
    c = c.drop_duplicates("service_request_id")
    c.loc[c["service_name"].str.contains("graffiti", case=False, na=False), "service_name"] = "Graffiti"
    c = c[c["case_age_days"] <= 730]          # <- outcome-based filter
    return c[c["date_requested"] >= "2023-01-01"]


def truncation_evidence(proper: pd.DataFrame, v1_test: pd.DataFrame) -> dict:
    """How much did v1's test set hide? Count the dropped requests and compare bucket mixes.

    Inputs
    ------
    proper  : the honest population, one row per cleaned request (closed AND open).
              Columns: ``service_request_id``, ``date_requested`` (datetime),
              ``source`` ("closed" or "open": which file the row came from) and
              ``bucket`` (the as-of label: "0-2", "3-7", "8-30", "31+", or NaN if not final).
    v1_test : v1's test set. Columns: ``service_request_id``, ``date_requested``,
              ``case_age_days`` (whole days to close; every v1 row is closed).

    Output: a dict with exactly these keys
    --------------------------------------
    h2_2025_all_requests          int   rows of ``proper`` submitted in [H2_START, H2_END)
    h2_2025_missing_from_v1_test  int   ... of those, how many ids do NOT appear in v1_test
    h2_2025_missing_share         float missing / all (0.0 if there are no H2 rows)
    h2_2025_missing_still_open    int   missing rows whose source is "open"
    q3_2025_n_proper              int   rows of ``proper`` submitted in [H2_START, Q3_END)
    q3_2025_n_v1                  int   rows of ``v1_test`` submitted in [H2_START, Q3_END)
    q3_2025_bucket_share_proper   dict  {bucket: share} of ``proper['bucket']`` over the Q3 rows
    q3_2025_bucket_share_v1_truncated
                                  dict  {bucket: share} of v1's Q3 rows, bucket taken from
                                        ``days_to_bucket(case_age_days)``

    Rules and edge cases
    --------------------
    * Windows are half-open: a request at exactly H2_START is in; one at exactly
      Q3_END or H2_END is out. Times of day matter (2025-06-30 23:59 is out).
    * "Missing" is decided by ``service_request_id`` only (a row is found if its id
      is anywhere in v1_test).
    * Bucket shares: the share among rows that HAVE a bucket (NaN buckets are left
      out of both numerator and denominator). Each share dict has all four BUCKETS as
      keys, in BUCKETS order, with 0.0 for a bucket that never occurs (also 0.0 for
      every bucket if no row has one). Round each share to 4 decimals.
    * Do not change the input frames.

    Hints
    -----
    1. Build two boolean masks on ``date_requested`` first (H2 and Q3). Everything else
       is a filter plus ``.isin`` / ``.sum()`` / ``.mean()``.
    2. ``Series.value_counts(normalize=True)`` ignores NaN; ``.reindex(BUCKETS,
       fill_value=0.0)`` gives you every bucket in order.
    3. ``days_to_bucket`` wants a float Series: ``days_to_bucket(days.astype(float))``.
    """
    t = proper["date_requested"]
    h2 = proper[(t >= H2_START) & (t < H2_END)]
    missing = ~h2["service_request_id"].isin(v1_test["service_request_id"])
    n_h2 = int(len(h2))

    def shares(buckets: pd.Series) -> dict:
        s = buckets.value_counts(normalize=True).reindex(BUCKETS, fill_value=0.0)
        return {b: round(float(s[b]), 4) for b in BUCKETS}

    q3 = h2[h2["date_requested"] < Q3_END]
    tv = v1_test["date_requested"]
    q3_v1 = v1_test[(tv >= H2_START) & (tv < Q3_END)]
    return {
        "h2_2025_all_requests": n_h2,
        "h2_2025_missing_from_v1_test": int(missing.sum()),
        "h2_2025_missing_share": float(missing.mean()) if n_h2 else 0.0,
        "h2_2025_missing_still_open": int((missing & (h2["source"] == "open")).sum()),
        "q3_2025_n_proper": int(len(q3)),
        "q3_2025_n_v1": int(len(q3_v1)),
        "q3_2025_bucket_share_proper": shares(q3["bucket"]),
        "q3_2025_bucket_share_v1_truncated": shares(days_to_bucket(q3_v1["case_age_days"].astype(float))),
    }


def compute(raw_dir=None) -> dict:
    """Every number in reports/old_vs_new.json."""
    v1 = v1_dataset(raw_dir)
    train = v1[v1["date_requested"] < "2025-01-01"]
    test = v1[v1["date_requested"] >= H2_START]
    y = test["case_age_days"]
    global_med = train["case_age_days"].median()
    svc_med = train.groupby("service_name")["case_age_days"].median()
    out = {
        "v1_n_train": len(train), "v1_n_test": len(test),
        "v1_global_median": float(global_med),
        "v1_mae_global_median": float((y - global_med).abs().mean()),
        "v1_mae_service_median": float((y - test["service_name"].map(svc_med).fillna(global_med)).abs().mean()),
    }

    # Proper population: cleaned union of closed + open (built by scripts/build_dataset.py)
    full = pd.read_parquet(DATASET, columns=["service_request_id", "date_requested", "date_closed",
                                             "source", "bucket"])
    out.update(truncation_evidence(full, test))

    # January-gap estimate, one year earlier: requests submitted Jan-Sep 2024,
    # still open on 2024-12-31, closed 2025-01-01..2025-01-14.
    prev = full[(full["date_requested"] >= "2024-01-01") & (full["date_requested"] < "2024-10-01")]
    gap = prev["date_closed"].between("2025-01-01", "2025-01-14")
    out["jan_gap_analog_2024_share"] = float(gap.mean())
    out["jan_gap_analog_2024_n"] = int(gap.sum())
    return out


def to_markdown(out: dict) -> str:
    v1b, proper = out["q3_2025_bucket_share_v1_truncated"], out["q3_2025_bucket_share_proper"]
    md = [
        "# Old (v1) vs new evaluation population",
        "",
        f"* v1 test set (closed by 2025-12-31, requested >= 2025-07-01, case_age <= 730): "
        f"**{out['v1_n_test']:,}** rows.",
        f"* v1 baselines reproduced: global train median ({out['v1_global_median']:.0f} days) MAE "
        f"**{out['v1_mae_global_median']:.2f}**, per-service train median MAE **{out['v1_mae_service_median']:.2f}**.",
        f"* All cleaned H2-2025 requests: **{out['h2_2025_all_requests']:,}**. Missing from the v1 test set: "
        f"**{out['h2_2025_missing_from_v1_test']:,} ({out['h2_2025_missing_share']:.1%})**, of which "
        f"{out['h2_2025_missing_still_open']:,} are still open in the snapshot.",
        "",
        "Bucket shares for requests submitted in Q3-2025 (all of these have a final label at 2025-12-31):",
        "",
        "| bucket | v1 truncated test | proper labels |", "|---|---|---|",
    ]
    for b in BUCKETS:
        md.append(f"| {b} | {v1b[b]:.1%} | {proper[b]:.1%} |")
    md += ["", f"January-gap analog: of requests submitted Jan-Sep 2024, {out['jan_gap_analog_2024_share']:.2%} "
           f"({out['jan_gap_analog_2024_n']:,}) closed between 2025-01-01 and 2025-01-14. The same kind of "
           "request from Jan-Sep 2025 is missing from both files, so our test sets probably lack a similar "
           "small share of (mostly 31+) cases."]
    return "\n".join(md) + "\n"


def _same(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def verify(raw_dir=None) -> int:
    """Recompute with the current code and compare with the committed report. 0 = match."""
    raw = Path(raw_dir or DEFAULT_RAW_DIR)
    if not DATASET.exists() or not list(raw.glob("get_it_done_requests_closed_*.slim.csv.gz")):
        print(f"SKIPPED real-data check: need {DATASET.relative_to(ROOT.parent)} (scripts/build_dataset.py, "
              f"after lesson 2) and the slim CSVs in {raw} (set SD311_DATA_DIR).")
        return 0
    want = json.loads(REPORT_JSON.read_text())
    got = compute(raw_dir)
    bad = [k for k in want if not _same(got.get(k), want[k])]
    for k in want:
        note = f"  (report: {want[k]})" if k in bad else ""
        print(f"{'DIFF' if k in bad else 'ok  '} {k}: {got.get(k)}{note}")
    if bad:
        print(f"\nFAIL: {len(bad)} number(s) differ from reports/old_vs_new.json")
        return 1
    print("\nPASS: your truncation_evidence reproduces every number in reports/old_vs_new.json")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None, help="folder with the slim CSVs (default: $SD311_DATA_DIR)")
    ap.add_argument("--verify", action="store_true", help="compare with the committed report, write nothing")
    args = ap.parse_args(argv)
    if args.verify:
        sys.exit(verify(args.data_dir))
    out = compute(args.data_dir)
    REPORT_JSON.write_text(json.dumps(out, indent=2, default=float))
    md = to_markdown(out)
    (ROOT / "reports" / "old_vs_new.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
