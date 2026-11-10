"""Rolling-origin backtest: model vs. baseline.

For each origin T (first day of a test quarter):

    |<-------- train (24 months) -------->|<- valid ->|<- embargo ->|<-- test (3 months) -->|
    T-24m                                 E-30d       E=T-31d       T                      T+3m

* The 31-day embargo means every train/valid request is at least 31 days
  old at T, so its bucket was already final (known) at T. Nothing from
  after T leaks into the labels we train on.
* The validation month is only used for early stopping.
* The baseline is fit on labels that became final in the 90 days before T.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .baseline import fit_baseline, predict_baseline
from .metrics import evaluate
from .train import fit_model, predict

EMBARGO_DAYS = 31


@dataclass
class Fold:
    """Date boundaries for one backtest fold (all half-open [start, end))."""
    train_start: pd.Timestamp
    valid_start: pd.Timestamp
    valid_end: pd.Timestamp   # = embargo start
    test_start: pd.Timestamp
    test_end: pd.Timestamp

    def as_dict(self) -> dict:
        return {k: str(v.date()) for k, v in self.__dict__.items()}


def make_fold(test_start, train_months: int = 24, test_months: int = 3) -> Fold:
    """Date boundaries of one backtest fold whose test period starts at ``test_start``.

    Called by ml/scripts/run_backtest.py (5 quarterly folds, each passed to ``run_fold``),
    ml/scripts/fit_final.py and databricks/tasks/train.py (backtest folds, and the final
    model's window ``make_fold(as_of + 1 day)``).

    With T = Timestamp(test_start) (accept a string or a Timestamp) return a ``Fold``:

        train_start = T - train_months calendar months      (pd.DateOffset(months=...))
        valid_end   = T - EMBARGO_DAYS days (31)            (the embargo starts here)
        valid_start = valid_end - 30 days
        test_start  = T
        test_end    = T + test_months calendar months

    All windows are half-open: train = [train_start, valid_start),
    valid = [valid_start, valid_end), test = [test_start, test_end).

    Why the 31-day embargo: a label can take up to 31 days to become final (a request
    is "31+" only once it is 31 days old). Every train/valid request is submitted before
    T - 31 days, so its label was already known at T. Without the gap, the newest
    training rows would carry labels from after T.

    Edge cases: months have different lengths, so use ``pd.DateOffset(months=n)`` for
    months and ``pd.Timedelta(days=n)`` for days (2024-03-01 minus 31 days is
    2024-01-30 in a leap year). Fields are ``pd.Timestamp``.

    Hints
    -----
    1. Read the ``Fold`` dataclass and the diagram at the top of this file first.
    2. Compute valid_end before valid_start; one depends on the other.
    """
    # TODO(Jaden): your code here. Delete the raise when you are done.
    raise NotImplementedError("[lesson 3] make_fold: valid_end = T - 31 days; DateOffset for months")


def _between(df, start, end):
    t = df["date_requested"]
    return df[(t >= start) & (t < end) & df["bucket"].notna()]


def run_fold(data: pd.DataFrame, fold: Fold, max_train_rows: int | None = 300_000,
             params: dict | None = None, seed: int = 0):
    """Train on one fold, score model and baseline on the test window.

    Returns (result dict, test-prediction frame).
    """
    train = _between(data, fold.train_start, fold.valid_start)
    valid = _between(data, fold.valid_start, fold.valid_end)
    test = _between(data, fold.test_start, fold.test_end)
    if max_train_rows and len(train) > max_train_rows:
        train = train.sample(max_train_rows, random_state=seed)  # compute budget (2 CPUs)

    model = fit_model(train, valid, params)
    m_pred, m_proba = predict(model, test)
    base = fit_baseline(data, fold.test_start)
    b_pred, b_proba = predict_baseline(base, test["service_name"])

    y = test["bucket"].astype(str).to_numpy()
    result = {
        "fold": fold.as_dict(),
        "n_train": int(len(train)), "n_valid": int(len(valid)), "n_test": int(len(test)),
        "best_iteration": int(model.get_best_iteration() or 0),
        "model": evaluate(y, m_pred, m_proba),
        "baseline": evaluate(y, b_pred, b_proba),
    }
    preds = pd.DataFrame({"service_request_id": test["service_request_id"].to_numpy(),
                          "date_requested": test["date_requested"].to_numpy(),
                          "service_name": test["service_name"].astype(str).to_numpy(),
                          "y": y, "model_pred": m_pred, "baseline_pred": b_pred,
                          "test_start": str(fold.test_start.date())})
    for i, b in enumerate(["0-2", "3-7", "8-30", "31+"]):
        preds[f"model_p_{b}"] = m_proba[:, i]
        preds[f"baseline_p_{b}"] = b_proba[:, i]
    return result, preds, model


def overall(preds: pd.DataFrame) -> dict:
    """Pool all folds' test predictions and evaluate once."""
    buckets = ["0-2", "3-7", "8-30", "31+"]
    out = {}
    for who in ("model", "baseline"):
        proba = preds[[f"{who}_p_{b}" for b in buckets]].to_numpy()
        out[who] = evaluate(preds["y"].to_numpy(), preds[f"{who}_pred"].to_numpy(), proba)
    return out


def quarterly_origins(as_of, n_folds: int = 4, test_months: int = 3) -> list[pd.Timestamp]:
    """The last ``n_folds`` quarter starts whose whole test window has final labels at ``as_of``.

    A test request submitted on day d has a final label by d + 31 days, so the
    test window must end on or before ``as_of - 31 days``. Oldest origin first.
    """
    last_ok_end = pd.Timestamp(as_of).normalize() - pd.Timedelta(days=EMBARGO_DAYS)
    q = pd.Timestamp(last_ok_end.year, 3 * ((last_ok_end.month - 1) // 3) + 1, 1)  # quarter start
    origins = []
    while len(origins) < n_folds:
        if q + pd.DateOffset(months=test_months) <= last_ok_end:
            origins.append(q)
        q = q - pd.DateOffset(months=3)
    return sorted(origins)
