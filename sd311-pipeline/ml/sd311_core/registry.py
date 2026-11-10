"""Small MLflow model-registry helpers (champion / challenger aliases).

On Databricks the registry is Unity Catalog (``mlflow.set_registry_uri("databricks-uc")``,
model name ``sd311.ml.closure_bucket``). In the tests it is a local MLflow file
store (``file:`` tracking URI), which supports the same alias calls.

The decision itself is :func:`sd311_core.monitoring.promotion_decision`; this
module only reads the current champion and writes the alias.
"""
from __future__ import annotations

from .monitoring import promotion_decision

CHAMPION = "champion"
CHALLENGER = "challenger"
# Metric logged on every training run; the next training run compares against it.
MEAN_LOG_LOSS_METRIC = "backtest_mean_log_loss"


def get_alias_version(client, model_name: str, alias: str):
    """Return the ModelVersion behind ``alias``, or None if the alias/model doesn't exist."""
    from mlflow.exceptions import MlflowException

    try:
        return client.get_model_version_by_alias(model_name, alias)
    except MlflowException:
        return None


def champion_mean_log_loss(client, model_name: str) -> float | None:
    """The champion's mean backtest log-loss, read from its training run (None if no champion)."""
    mv = get_alias_version(client, model_name, CHAMPION)
    if mv is None or not mv.run_id:
        return None
    return client.get_run(mv.run_id).data.metrics.get(MEAN_LOG_LOSS_METRIC)


def assign_alias(client, model_name: str, version: str, fold_results: list[dict]) -> dict:
    """Decide champion vs challenger for ``version`` and set the alias.

    Returns the decision dict (alias, reason, ...), also written as version tags
    so the reason is visible in the registry UI.
    """
    decision = promotion_decision(fold_results, champion_mean_log_loss(client, model_name))
    client.set_registered_model_alias(model_name, decision["alias"], str(version))
    client.set_model_version_tag(model_name, str(version), "promotion_reason", decision["reason"])
    return decision
