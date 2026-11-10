"""Is a field filled in at submission time, or later?

Idea: the newest open requests (submitted in the last few days before the
open-file extract) show what a record looks like *shortly after
submission*. Closed requests show what it looks like *after the work is
done*. If a field is much more often missing for the newest requests than
for closed requests of the same services, it is probably filled in later,
which makes it unsafe as a submission-time feature.

To compare like with like we reweight the closed comparison group so it
has the same service mix as the newest open group (a simple
"direct standardisation": per-service rate, weighted by the open group's
service shares).

This is EVIDENCE, not proof: newest-open requests differ from closed ones
in other ways too (e.g. requests that get closed in 0-2 days never appear
in the open file at all).

Usage:  python analysis/field_availability.py
Writes: reports/field_availability.md
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sd311_core.cleaning import dedupe_prefer_closed  # noqa: E402
from sd311_core.io import DEFAULT_RAW_DIR, load_all  # noqa: E402

FIELDS = {
    "service_name_detail missing": lambda d: d["service_name_detail"].isna(),
    "case_record_type missing": lambda d: d["case_record_type"].isna(),
    "parent id present (is_followup)": lambda d: d["service_request_parent_id"].notna(),
    "desc_len == 0": lambda d: d["desc_len"] == 0,
}


def standardised_rate(flag: pd.Series, service: pd.Series, weights: pd.Series) -> float:
    """Per-service mean of ``flag``, averaged with the given service weights."""
    per_service = flag.groupby(service, observed=True).mean()
    w = weights[weights.index.isin(per_service.index)]
    return float((per_service.reindex(w.index) * w).sum() / w.sum())


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None,
                    help="folder with the slim CSVs (default: $SD311_DATA_DIR or ml/data/raw)")
    args = ap.parse_args(argv)
    raw, extract = load_all(args.data_dir or DEFAULT_RAW_DIR)
    df = dedupe_prefer_closed(raw)
    df = df[(df["status"] != "Referred") & (df["service_name"] != "Other")]
    age = (extract - df["date_requested"].dt.normalize()).dt.days

    closed_ref = df[(df["source"] == "closed") & (df["date_requested"] >= "2025-07-01")]
    lines = [f"# Field availability (extract date {extract.date()})", "",
             "Closed comparison group: closed requests submitted 2025-07-01 or later, "
             "reweighted to the service mix of each open group.", "",
             "A second comparison group keeps only closed requests that took >= 3 days, because a "
             "request that is closed within a day or two can never show up in the open file "
             "(survivor bias).", "",
             "| field | open, last 3 days | closed, same mix | closed >=3d, same mix "
             "| open, last 14 days | closed, same mix |",
             "|---|---|---|---|---|---|"]
    took = (closed_ref["date_closed"] - closed_ref["date_requested"].dt.normalize()).dt.days
    slow_ref = closed_ref[took >= 3]
    groups = {}
    for days in (3, 14):
        g = df[(df["source"] == "open") & (age >= 0) & (age < days)]
        groups[days] = g
    for name, fn in FIELDS.items():
        row = [name]
        for days, g in groups.items():
            w = g["service_name"].value_counts(normalize=True)
            row.append(f"{fn(g).mean():.1%} (n={len(g):,})")
            row.append(f"{standardised_rate(fn(closed_ref), closed_ref['service_name'], w):.1%}")
            if days == 3:
                row.append(f"{standardised_rate(fn(slow_ref), slow_ref['service_name'], w):.1%}")
        lines.append("| " + " | ".join(row) + " |")

    # Does case_record_type differ *within* a service between groups?
    g = groups[14]
    top_type = closed_ref.groupby("service_name")["case_record_type"].agg(
        lambda s: s.mode().iat[0] if s.notna().any() else None)
    same_open = (g["case_record_type"] == g["service_name"].map(top_type)).mean()
    w = g["service_name"].value_counts(normalize=True)
    same_closed = standardised_rate(closed_ref["case_record_type"] == closed_ref["service_name"].map(top_type),
                                    closed_ref["service_name"], w)
    lines += ["", f"Share of rows whose case_record_type equals the service's usual type: "
              f"open-14d {same_open:.1%} vs closed (same mix) {same_closed:.1%}."]
    text = "\n".join(lines)
    print(text)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "field_availability.md").write_text(text + "\n")


if __name__ == "__main__":
    main()
