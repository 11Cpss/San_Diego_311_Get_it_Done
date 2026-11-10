"""ml/scripts/make_slim.py: full city CSV -> slim .slim.csv.gz that sd311_core.io reads."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import make_slim  # noqa: E402

from sd311_core.io import load_open  # noqa: E402


def test_make_slim_roundtrip(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "raw"
    src.mkdir()
    full = pd.DataFrame({c: ["x", "y"] for c in make_slim.KEEP})
    full["service_request_id"] = ["00123", "456"]          # leading zero must survive
    full["date_requested"] = ["2026-01-10 08:00:00", "2026-01-11 09:30:00"]
    full["date_closed"] = ["", ""]
    full["case_age_days"] = ["4", "3"]
    full["lat"], full["lng"] = ["32.7", "32.8"], ["-117.1", "-117.2"]
    full["public_description"] = ["pothole on 5th", None]
    full["address"] = ["1 Main St", "2 Main St"]          # dropped
    full.to_csv(src / "get_it_done_requests_open_datasd.csv", index=False)
    (src / "unrelated.csv").write_text("a\n1\n")

    make_slim.main(["--src", str(src), "--data-dir", str(dst)])
    out = dst / "get_it_done_requests_open_datasd.slim.csv.gz"
    slim = pd.read_csv(out, dtype=str)
    assert list(slim.columns) == make_slim.KEEP + ["desc_len"]
    assert slim["desc_len"].tolist() == ["14", "0"]
    assert slim["service_request_id"].tolist() == ["00123", "456"]
    assert len(list(dst.iterdir())) == 1

    df = load_open(dst)                                      # the model code can read it
    assert df["desc_len"].tolist() == [14, 0] and (df["source"] == "open").all()
