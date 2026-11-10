"""Champion/challenger aliases against a real (local, file-based) MLflow registry.

Same client calls as on Databricks; only the tracking/registry URI differs
(Unity Catalog there, a temporary folder here). assign_alias calls your
promotion_decision (lesson 8), so this module is marked lesson8.
"""
import pytest

mlflow = pytest.importorskip("mlflow")

pytestmark = pytest.mark.lesson8

from sd311_core.registry import (  # noqa: E402
    CHALLENGER,
    CHAMPION,
    MEAN_LOG_LOSS_METRIC,
    assign_alias,
    champion_mean_log_loss,
    get_alias_version,
)

NAME = "closure_bucket_test"


def fold(model_f1, base_f1, ll):
    return {"model": {"macro_f1": model_f1, "log_loss": ll}, "baseline": {"macro_f1": base_f1}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    # MLflow 3.x keeps the file store in "maintenance mode" and refuses it unless
    # this is set. Fine for a throwaway test registry (sqlite would also work).
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    uri = (tmp_path / "mlruns").as_uri()          # file:///tmp/.../mlruns
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    c = mlflow.MlflowClient(tracking_uri=uri, registry_uri=uri)
    c.create_registered_model(NAME)
    yield c
    mlflow.set_tracking_uri(None)


def new_version(client, folds):
    """Log a training run like train.py does (metric + a stand-in artifact) and register it."""
    import numpy as np

    with mlflow.start_run() as run:
        mlflow.log_metric(MEAN_LOG_LOSS_METRIC, float(np.mean([f["model"]["log_loss"] for f in folds])))
        mlflow.log_dict({"note": "stand-in for the CatBoost model"}, "model/info.json")
    src = f"{run.info.artifact_uri}/model"
    mv = client.create_model_version(NAME, src, run_id=run.info.run_id)
    return mv.version, assign_alias(client, NAME, mv.version, folds)


def test_champion_then_challenger_then_new_champion(client):
    assert get_alias_version(client, NAME, CHAMPION) is None
    assert champion_mean_log_loss(client, NAME) is None

    v1, d1 = new_version(client, [fold(0.53, 0.42, 0.95), fold(0.52, 0.40, 0.95)])
    assert d1["alias"] == CHAMPION
    assert get_alias_version(client, NAME, CHAMPION).version == v1
    assert champion_mean_log_loss(client, NAME) == pytest.approx(0.95)

    # worse log-loss -> challenger; champion unchanged
    v2, d2 = new_version(client, [fold(0.55, 0.42, 0.99)])
    assert d2["alias"] == CHALLENGER
    assert get_alias_version(client, NAME, CHAMPION).version == v1
    assert get_alias_version(client, NAME, CHALLENGER).version == v2
    assert "log-loss" in client.get_model_version(NAME, v2).tags["promotion_reason"]

    # better log-loss but loses to the baseline in one fold -> still challenger
    v3, d3 = new_version(client, [fold(0.55, 0.42, 0.80), fold(0.30, 0.31, 0.80)])
    assert d3["alias"] == CHALLENGER and get_alias_version(client, NAME, CHAMPION).version == v1

    # better on both -> new champion
    v4, d4 = new_version(client, [fold(0.55, 0.42, 0.90)])
    assert d4["alias"] == CHAMPION and get_alias_version(client, NAME, CHAMPION).version == v4
