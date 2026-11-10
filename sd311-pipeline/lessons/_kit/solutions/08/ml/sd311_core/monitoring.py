"""Monitoring helpers: drift (PSI), live metrics and the promotion rule.

All functions are pandas/dict in, dict/float out, so they can be unit tested
without Spark or MLflow (ml/tests/test_monitoring.py).
"""
from __future__ import annotations

import math  # noqa: F401  (you use it in psi, lesson 8)

import numpy as np  # noqa: F401  (you use it in promotion_decision, lesson 8)
import pandas as pd

from .labels import BUCKETS, make_labels
from .metrics import evaluate

# Columns whose mix we watch for drift. Both are model inputs and both can shift
# for operational reasons (a new app version changes case_origin, a new service
# category appears, a storm floods one service).
DRIFT_COLUMNS = ["service_name", "case_origin"]

# Common rule of thumb for PSI: < 0.1 stable, 0.1-0.25 moderate shift, > 0.25 big shift.
PSI_ALARM_THRESHOLD = 0.25

OTHER = "__other__"
_EPS = 1e-4  # floor for empty categories, so ln(0) never happens


def category_shares(values: pd.Series) -> dict:
    """{category: share}; missing values count as the category 'Unknown'."""
    s = values.astype("object").where(values.notna(), "Unknown").astype(str)
    return {k: float(v) for k, v in s.value_counts(normalize=True).items()}


def reference_distributions(train_df: pd.DataFrame, columns=DRIFT_COLUMNS) -> dict:
    """Training-time shares per drift column (logged to MLflow as JSON)."""
    return {c: category_shares(train_df[c]) for c in columns}


def psi(reference: dict, actual: dict, eps: float = _EPS) -> float:
    """Population Stability Index between two {category: share} dicts.

    Called by ``drift_report`` (one PSI per watched column), which
    databricks/tasks/evaluate_and_summarize.py runs every day: last 7 days of
    requests vs the champion's training mix. The largest value becomes
    ``drift.max_psi`` in run_summary.json and the CloudWatch ``MaxPSI`` alarm.

    Input
    -----
    reference : {category: share} at training time (the "expected" mix, e).
    actual    : {category: share} now (a). Shares in each dict sum to about 1.
    eps       : floor for tiny/empty shares (1e-4).

    Output: one float >= 0.  PSI = sum over categories of (a - e) * ln(a / e).
    0 means identical mixes; > 0.25 is a big shift (PSI_ALARM_THRESHOLD).

    Rules
    -----
    1. Pool unseen categories: every category of ``actual`` that is NOT a key of
       ``reference`` is added up into one category named OTHER ("__other__").
       So a brand-new service shows up as drift instead of being ignored.
    2. Sum over the union of categories (reference keys plus OTHER if it got any share).
       A category missing on one side has share 0 there.
    3. Floor: use max(share, eps) on both sides before the formula, so ln(0) and
       division by zero never happen.
    4. Return a plain Python float. Do not modify the input dicts.

    Worked example: reference {a: .5, b: .5}, actual {a: .8, b: .2}
    -> (.8-.5)*ln(.8/.5) + (.2-.5)*ln(.2/.5) = 0.141 + 0.275 = 0.416.

    Hints
    -----
    1. First build a new dict for ``actual`` with the unseen keys merged into OTHER.
    2. Then loop over ``set(reference) | set(pooled_actual)``; ``dict.get(k, 0.0)``
       handles the missing side. ``math.log`` is the natural log.
    """
    ref = dict(reference)
    act: dict = {}
    for k, v in actual.items():
        key = k if k in ref else OTHER
        act[key] = act.get(key, 0.0) + v
    total = 0.0
    for k in set(ref) | set(act):
        e = max(ref.get(k, 0.0), eps)
        a = max(act.get(k, 0.0), eps)
        total += (a - e) * math.log(a / e)
    return float(total)


def drift_report(recent: pd.DataFrame, reference: dict, columns=DRIFT_COLUMNS) -> dict | None:
    """{"max_psi": float, "psi_by_feature": {...}} or None if nothing to compare."""
    if recent is None or recent.empty or not reference:
        return None
    by = {c: round(psi(reference[c], category_shares(recent[c])), 4)
          for c in columns if c in reference and c in recent.columns}
    if not by:
        return None
    return {"max_psi": max(by.values()), "psi_by_feature": by}


def live_metrics(predictions: pd.DataFrame, requests: pd.DataFrame, as_of,
                 window_days: int = 28) -> dict:
    """Macro-F1 and log-loss on predictions whose labels became final recently.

    predictions: service_request_id, snapshot_date, pred_bucket, p_0_2..p_31_plus
                 (several rows per id are allowed; the FIRST snapshot is used,
                 because that is the prediction made at submission time)
    requests:    service_request_id, date_requested, date_closed (latest state)
    as_of:       the day we evaluate on. Labels come from sd311_core.labels.make_labels,
                 so a label counts only once it can no longer change.

    Only labels with label_final_at in (as_of - window_days, as_of] are used.
    Returns {"macro_f1", "log_loss", "n"}; metrics are None when n == 0.
    """
    from .serving import PROBA_COLUMNS

    as_of = pd.Timestamp(as_of).normalize()
    empty = {"macro_f1": None, "log_loss": None, "n": 0}
    if predictions.empty or requests.empty:
        return empty
    first = (predictions.sort_values("snapshot_date")
             .drop_duplicates("service_request_id", keep="first"))
    req = requests[["service_request_id", "date_requested", "date_closed"]].copy()
    req["service_request_id"] = req["service_request_id"].astype(str)
    req["date_requested"] = pd.to_datetime(req["date_requested"])
    req["date_closed"] = pd.to_datetime(req["date_closed"]).dt.normalize()
    lab = req.join(make_labels(req, as_of))
    # For a 0-30 day request label_final_at is "end of the close day" (close date
    # + 1 day), so one closed on as_of is final at as_of + 1 day: allow up to the
    # end of as_of. ("31+" labels are final at date_requested + 31 days.)
    end = as_of + pd.Timedelta(days=1)
    recent = lab[lab["bucket"].notna()
                 & (lab["label_final_at"] > end - pd.Timedelta(days=window_days))
                 & (lab["label_final_at"] <= end)]
    first = first.assign(service_request_id=first["service_request_id"].astype(str))
    joined = first.merge(recent[["service_request_id", "bucket"]], on="service_request_id")
    if joined.empty:
        return empty
    proba = joined[[PROBA_COLUMNS[b] for b in BUCKETS]].to_numpy(dtype="float64")
    m = evaluate(joined["bucket"].astype(str).to_numpy(), joined["pred_bucket"].astype(str).to_numpy(), proba)
    return {"macro_f1": round(m["macro_f1"], 4), "log_loss": round(m["log_loss"], 4), "n": m["n"]}


def promotion_decision(fold_results: list[dict], champion_mean_log_loss: float | None) -> dict:
    """Champion/challenger rule: should the newly trained model serve tomorrow? Pure function.

    Called by ``registry.assign_alias`` at the end of databricks/tasks/train.py. The
    returned "alias" is set on the new model version in the MLflow registry (the
    daily score task loads ``@champion``), and "reason" is written as a version tag and
    logged as promotion_decision.json.

    Input
    -----
    fold_results : one dict per backtest fold (as returned by backtest.run_fold); only
                   ``f["model"]["macro_f1"]``, ``f["model"]["log_loss"]`` and
                   ``f["baseline"]["macro_f1"]`` are used.
    champion_mean_log_loss : the current champion's mean backtest log-loss, or None if
                   there is no champion yet.

    Output: a dict with these keys
    ------------------------------
    alias                      "champion" or "challenger"
    mean_log_loss              float, mean of the model log-loss over the folds
    beats_baseline_every_fold  bool
    min_f1_margin              float, min over folds of (model macro-F1 - baseline macro-F1)
    reason                     str, a human-readable explanation (see below)

    Rules
    -----
    Promote ("champion") only if BOTH hold:
      1. the model's macro-F1 is strictly greater than the baseline's in EVERY fold
         (a tie is not a win), and
      2. its mean log-loss is strictly lower than the champion's, or there is no
         champion (None). An equal log-loss is not better.
    Otherwise the alias is "challenger".

    The reason string must say why (tests look for these words):
      * when rule 1 fails: include ``fold {i}``, where i is the 0-based index of the fold
        with the smallest margin (the first such fold on a tie), and its margin;
      * when rule 2 fails: include the word ``log-loss`` and both numbers;
      * when promoted: say why too (e.g. "no current champion" or the two log-losses).
    No folds at all -> {"alias": "challenger", "mean_log_loss": None,
    "beats_baseline_every_fold": False, "reason": "no backtest folds"} (no min_f1_margin).

    Hints
    -----
    1. Compute the list of margins once; ``all(...)``, ``min(...)`` and
       ``int(np.argmin(...))`` answer the three questions about it.
    2. Collect the failure messages in a list and ``"; ".join`` them, so a model that
       fails both rules explains both.
    """
    if not fold_results:
        return {"alias": "challenger", "mean_log_loss": None,
                "beats_baseline_every_fold": False, "reason": "no backtest folds"}
    mean_ll = float(np.mean([f["model"]["log_loss"] for f in fold_results]))
    margins = [f["model"]["macro_f1"] - f["baseline"]["macro_f1"] for f in fold_results]
    beats_all = all(m > 0 for m in margins)
    better_ll = champion_mean_log_loss is None or mean_ll < champion_mean_log_loss
    if beats_all and better_ll:
        alias = "champion"
        reason = ("no current champion" if champion_mean_log_loss is None else
                  f"log-loss {mean_ll:.4f} < champion {champion_mean_log_loss:.4f}")
        reason += f"; beats baseline macro-F1 in all {len(margins)} folds"
    else:
        alias = "challenger"
        parts = []
        if not beats_all:
            worst = int(np.argmin(margins))
            parts.append(f"macro-F1 does not beat baseline in fold {worst} (margin {margins[worst]:+.4f})")
        if not better_ll:
            parts.append(f"log-loss {mean_ll:.4f} >= champion {champion_mean_log_loss:.4f}")
        reason = "; ".join(parts)
    return {"alias": alias, "mean_log_loss": mean_ll, "beats_baseline_every_fold": beats_all,
            "min_f1_margin": float(min(margins)), "reason": reason}
