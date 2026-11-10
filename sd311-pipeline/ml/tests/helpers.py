"""Tiny hand-made request frames for tests."""
import pandas as pd


def make_requests(rows):
    """rows: list of dicts with at least date_requested and optional date_closed/service_name."""
    base = {"service_request_id": None, "service_request_parent_id": None,
            "service_name": "Pothole", "comm_plan_name": "Uptown", "council_district": "3",
            "case_origin": "Web", "case_record_type": "TSW", "desc_len": 10,
            "date_closed": None, "source": "closed", "status": "Closed"}
    out = []
    for i, r in enumerate(rows):
        row = dict(base)
        row["service_request_id"] = str(i + 1)
        row.update(r)
        out.append(row)
    df = pd.DataFrame(out)
    df["date_requested"] = pd.to_datetime(df["date_requested"])
    df["date_closed"] = pd.to_datetime(df["date_closed"])
    return df
