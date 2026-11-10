"""A strong-ish, honest baseline.

For each service: look at the labels that became final in the 90 days
before the test period starts. Predict that service's most common bucket;
use its bucket shares as probabilities. Services with fewer than
``min_count`` final labels fall back to the global (all-services) mix.

This is what a dispatcher with a spreadsheet could do, so the model has
to beat it to be worth anything.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .labels import BUCKETS


def fit_baseline(data: pd.DataFrame, period_start, window_days: int = 90, min_count: int = 30) -> dict:
    """Per-service bucket mix from the outcomes known at ``period_start``.

    Called by ``backtest.run_fold`` (baseline for each fold, fit at the fold's test
    start T) and so by ml/scripts/run_backtest.py and databricks/tasks/train.py.
    ``predict_baseline`` below turns the result into predictions.

    Input
    -----
    data         : one row per request with ``service_name``, ``bucket`` (one of BUCKETS
                   or NaN) and ``label_final_at`` (timestamp, NaT if no label).
    period_start : T, the first moment of the period we will predict.
    window_days  : look back this many days (90).
    min_count    : a service needs at least this many known labels (30) to get its own mix.

    Output
    ------
    {"global": array of 4 floats, "by_service": {service (str): array of 4 floats},
     "period_start": "YYYY-MM-DD"}. Every array is in BUCKETS order and sums to 1.

    Rules
    -----
    * Use only rows whose label was known at T and is recent:
      ``T - window_days < label_final_at <= T`` (left end open, right end closed)
      and bucket not NaN. Nothing with label_final_at after T: that is the future.
    * global = the bucket shares over ALL those rows (every service together,
      including services that are too small to get their own mix).
    * by_service[s] = the bucket shares among service s's rows, only for services with
      >= min_count such rows. Smaller services are left out of the dict
      (predict_baseline then falls back to "global").
    * Service keys are strings (``str(service_name)``), even if the column is categorical.
    * A bucket with no rows gets share 0.0 (it is still one of the 4 entries).
    You may assume at least one row falls in the window.

    Hints
    -----
    1. One boolean mask for the window, then ``pd.crosstab(service, bucket)``
       gives a count table; ``.reindex(columns=BUCKETS, fill_value=0)`` fixes the column
       order and adds missing buckets.
    2. Row sums of that table are the per-service n; column sums are the global counts.
    3. Divide counts by their total to get shares (``.to_numpy(dtype="float64")``).
    """
    # TODO(Jaden): your code here. Delete the raise when you are done.
    raise NotImplementedError("[lesson 3] fit_baseline: labels final in (T-90d, T], crosstab, min_count")


def predict_baseline(model: dict, services: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Return (predicted bucket labels, probability matrix in BUCKETS order)."""
    proba = np.vstack([model["by_service"].get(s, model["global"]) for s in services.astype(str)])
    pred = np.array(BUCKETS)[proba.argmax(axis=1)]
    return pred, proba
