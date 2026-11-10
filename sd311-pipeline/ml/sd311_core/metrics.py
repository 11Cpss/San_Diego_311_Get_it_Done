"""Evaluation metrics shared by the backtest, the baseline and the reports."""
from __future__ import annotations

import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, log_loss, recall_score

from .labels import BUCKETS


def _to_codes(labels) -> list[int]:
    """Bucket strings -> 0..3 in BUCKETS order."""
    index = {b: i for i, b in enumerate(BUCKETS)}
    return [index[str(v)] for v in labels]


def evaluate(y_true, y_pred, proba: np.ndarray) -> dict:
    """Compute the standard metric set.

    ``proba`` has one column per bucket, in ``BUCKETS`` order.
    Probabilities are clipped to [1e-6, 1] before log-loss so a single
    impossible-looking prediction cannot make the loss infinite.
    """
    proba = np.clip(np.asarray(proba, dtype="float64"), 1e-6, 1.0)
    proba = proba / proba.sum(axis=1, keepdims=True)
    recalls = recall_score(y_true, y_pred, labels=BUCKETS, average=None, zero_division=0)
    return {
        "n": int(len(y_true)),
        "macro_f1": float(f1_score(y_true, y_pred, labels=BUCKETS, average="macro", zero_division=0)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        # sklearn's log_loss assumes columns in *sorted* label order, and
        # sorted strings would put "31+" before "8-30". Use integer codes.
        "log_loss": float(log_loss(np.asarray(_to_codes(y_true)), proba, labels=[0, 1, 2, 3])),
        "recall": {b: float(r) for b, r in zip(BUCKETS, recalls, strict=True)},
        "confusion": confusion_matrix(y_true, y_pred, labels=BUCKETS).tolist(),
    }
