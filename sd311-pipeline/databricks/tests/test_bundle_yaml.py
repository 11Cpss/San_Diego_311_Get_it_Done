"""Offline checks of databricks.yml.

We cannot run `databricks bundle validate` without a workspace, so we:
  * parse the YAML,
  * feed each job into the typed `databricks-bundles` Python models, which reject
    unknown/misspelled fields (e.g. `enviroment_key`),
  * check the task graph is the intended chain and every python_file exists.
"""
from pathlib import Path

import pytest
import yaml

BUNDLE_DIR = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((BUNDLE_DIR / "databricks.yml").read_text())
JOBS = CFG["resources"]["jobs"]


def test_targets_and_variables():
    assert set(CFG["targets"]) == {"dev", "prod"}
    assert CFG["targets"]["dev"]["mode"] == "development"
    assert CFG["targets"]["prod"]["mode"] == "production"
    assert {"warehouse_id", "notification_email", "catalog"} <= set(CFG["variables"])


def test_daily_task_chain():
    tasks = JOBS["sd311_daily"]["tasks"]
    order = [t["task_key"] for t in tasks]
    assert order == ["load_bronze", "snapshots", "dbt_build", "features", "score",
                     "evaluate_and_summarize"]
    for prev, t in zip(order, tasks[1:], strict=False):   # each task depends on the one before
        assert t["depends_on"] == [{"task_key": prev}]
    dbt = tasks[2]["dbt_task"]
    assert dbt["warehouse_id"] == "${var.warehouse_id}"
    assert (BUNDLE_DIR / dbt["project_directory"] / "dbt_project.yml").exists()


def test_train_job_is_manual():
    assert "schedule" not in JOBS["sd311_train"] and "trigger" not in JOBS["sd311_train"]


def test_failure_emails_and_serverless_envs():
    for job in JOBS.values():
        assert job["email_notifications"]["on_failure"] == ["${var.notification_email}"]
        env_keys = {e["environment_key"] for e in job["environments"]}
        for t in job["tasks"]:
            assert t["environment_key"] in env_keys       # serverless: env, no cluster
            assert "job_cluster_key" not in t and "new_cluster" not in t
            if "spark_python_task" in t:
                assert (BUNDLE_DIR / t["spark_python_task"]["python_file"]).exists()


@pytest.mark.parametrize("job_name", sorted(JOBS))
def test_job_matches_typed_schema(job_name):
    jobs = pytest.importorskip("databricks.bundles.jobs")
    jobs.Job.from_dict(JOBS[job_name])  # raises ValueError on unknown fields
