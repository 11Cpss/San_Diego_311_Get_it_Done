"""Static checks of .github/workflows/ci.yml (lesson 9: you write the CI workflow).

GitHub is not needed: the YAML is parsed and checked against the contract in
lessons/09_ci_cd.md. Jobs are found by what they DO (their steps), so you can
name them however you like.

Tests skip while ci.yml still says "LESSON 9 STUB";
`python scripts/lessons.py check 9` runs them anyway (and fails on the stub).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - depends on the environment
    yaml = None

pytestmark = pytest.mark.lesson9

CI = ".github/workflows/ci.yml"
CI_PATH = Path(__file__).resolve().parents[2] / CI


# ----------------------------------------------------------------- helpers
@pytest.fixture
def wf(request, skip_if_lesson_pending):
    skip_if_lesson_pending(CI, 9)
    if yaml is None:
        if request.config.getoption("--lesson-strict"):
            pytest.fail("pyyaml is not installed: pip install pyyaml")
        pytest.skip("pyyaml is not installed")
    data = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and data.get("jobs"), "ci.yml needs a top-level `jobs:` map"
    return data


def triggers(wf) -> dict:
    # YAML 1.1 (PyYAML) reads the bare key `on` as the boolean True.
    on = wf.get("on", wf.get(True))
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return dict.fromkeys(on)
    return on or {}


def steps(job) -> list[dict]:
    return job.get("steps") or []


def runs(job) -> str:
    return "\n".join(s.get("run", "") for s in steps(job))


def uses(job) -> list[str]:
    return [s["uses"] for s in steps(job) if "uses" in s]


def find_job(wf, predicate, what):
    found = [(name, job) for name, job in wf["jobs"].items() if predicate(job)]
    assert found, f"no job {what}"
    return found[0][1]


def workdir_of(job, keyword) -> set[str]:
    """Where commands containing `keyword` run: job default, step override or -chdir/cd."""
    default = ((job.get("defaults") or {}).get("run") or {}).get("working-directory")
    dirs = set()
    for s in steps(job):
        if keyword in s.get("run", ""):
            dirs.add(s.get("working-directory") or default or ".")
            m = re.search(r"-chdir=(\S+)|\bcd\s+(\S+)", s["run"])
            if m:
                dirs.add((m.group(1) or m.group(2)).strip("/"))
    return dirs


def setup_with(job, action) -> dict:
    for s in steps(job):
        if s.get("uses", "").split("@")[0] == action:
            return s.get("with") or {}
    raise AssertionError(f"job does not use {action}")


# ----------------------------------------------------------------- the workflow
def test_runs_on_every_pull_request_and_push(wf):
    on = triggers(wf)
    assert "pull_request" in on and "push" in on, f"triggers are {sorted(map(str, on))}"
    assert not (on.get("push") or {}).get("branches"), "CI should check pushes to every branch"


def test_read_only_permissions_and_no_secrets(wf):
    assert wf.get("permissions") == {"contents": "read"}
    text = CI_PATH.read_text(encoding="utf-8")
    assert "secrets." not in text, "CI must not need secrets (only deploy.yml does)"
    assert "id-token" not in text, "CI never assumes an AWS role"
    for name, job in wf["jobs"].items():
        perms = job.get("permissions") or {}
        assert "write" not in str(perms), f"job {name} asks for write permissions"


def test_every_job_is_complete_and_actions_are_pinned(wf):
    for name, job in wf["jobs"].items():
        assert job.get("runs-on"), f"job {name} has no runs-on"
        assert steps(job), f"job {name} has no steps"
        for ref in uses(job):
            assert re.search(r"@(v\d[\w.]*|[0-9a-f]{40})$", ref), (
                f"{ref}: pin actions to a version tag or commit SHA, not a branch")


# ----------------------------------------------------------------- python job
def python_job(wf):
    return find_job(wf, lambda j: "ruff check" in runs(j) and "pytest" in runs(j),
                    "runs both `ruff check` and `pytest`")


def test_python_job_lints_then_tests(wf):
    job = python_job(wf)
    commands = [s.get("run", "") for s in steps(job)]
    ruff = next(i for i, c in enumerate(commands) if "ruff check" in c)
    pytest_ = next(i for i, c in enumerate(commands) if re.search(r"\bpytest\b", c))
    assert ruff <= pytest_, "lint first: it is the fastest feedback"


def test_python_job_matches_the_lambda_python_and_has_java(wf):
    job = python_job(wf)
    assert str(setup_with(job, "actions/setup-python").get("python-version")) == "3.12"
    assert str(setup_with(job, "actions/setup-java").get("java-version")) == "17"


def test_python_job_installs_everything_the_tests_import(wf):
    text = runs(python_job(wf))
    assert re.search(r"pip install -e \"?\.\[(dev,ml|ml,dev)\]\"?", text), (
        'install the repo editable with its extras: pip install -e ".[dev,ml]"')
    assert "databricks/requirements-dev.txt" in text
    assert "python-hcl2" in text and "dbt-duckdb" in text


# ----------------------------------------------------------------- docker job
def test_docker_job_builds_but_never_pushes(wf):
    job = find_job(wf, lambda j: "docker build" in runs(j), "runs `docker build`")
    assert re.search(r"docker build .*-f docker/Dockerfile.* \.(\s|$)", runs(job)), (
        "build docker/Dockerfile with the repo root as the context: "
        "docker build -f docker/Dockerfile -t <tag> .")
    text = CI_PATH.read_text(encoding="utf-8")
    assert "docker push" not in text and "amazon-ecr-login" not in text


# ----------------------------------------------------------------- terraform job
def test_terraform_job_formats_inits_without_backend_and_validates(wf):
    job = find_job(wf, lambda j: "terraform validate" in runs(j), "runs `terraform validate`")
    assert str(setup_with(job, "hashicorp/setup-terraform").get("terraform_version")) == "1.9.8"
    text = runs(job)
    assert re.search(r"terraform fmt .*-check", text), "terraform fmt -check"
    assert "terraform init -backend=false" in text, "init without a backend (no credentials)"
    for keyword in ("terraform fmt", "terraform init", "terraform validate"):
        assert workdir_of(job, keyword) == {"infra"}, f"`{keyword}` must run in infra/"


# ----------------------------------------------------------------- dbt job
def test_dbt_job_builds_on_duckdb(wf):
    job = find_job(wf, lambda j: "dbt build" in runs(j), "runs `dbt build`")
    text = runs(job)
    assert "dbt-duckdb" in text
    assert str(setup_with(job, "actions/setup-python").get("python-version")) == "3.12"
    assert re.search(r"dbt build .*--target duckdb", text), "dbt build --target duckdb"
    assert "dbt parse" in text
    assert workdir_of(job, "dbt build") == {"dbt"}, "run dbt inside dbt/"
    env = {**(wf.get("env") or {}), **(job.get("env") or {})}
    for s in steps(job):
        env.update(s.get("env") or {})
    assert "--profiles-dir" in text or env.get("DBT_PROFILES_DIR") == ".", (
        "point dbt at dbt/profiles.yml (--profiles-dir . or DBT_PROFILES_DIR=.)")
