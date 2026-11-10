"""Static checks of infra/main.tf (lesson 5: you write the ingest IAM policy and schedule).

No Terraform binary and no AWS account needed: the HCL is parsed with
python-hcl2 (`pip install python-hcl2`) and the parsed blocks are checked
against the least-privilege rules in lessons/05_terraform_and_iam.md.

Tests marked `lesson5` skip while infra/main.tf still says "LESSON 5 STUB";
`python scripts/lessons.py check 5` runs them anyway (and fails on the stub).
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import hcl2
except ImportError:  # pragma: no cover - depends on the environment
    hcl2 = None

INFRA = Path(__file__).resolve().parents[2] / "infra"
MAIN_TF = "infra/main.tf"
BUCKET = "aws_s3_bucket.data.arn"

# Every action the ingest Lambda really calls (see src/sd311/ingest.py).
ALLOWED_ACTIONS = {
    "s3:PutObject", "s3:GetObject", "s3:ListBucket",
    "ssm:GetParameter", "ssm:GetParameters",
    "cloudwatch:PutMetricData",
    "logs:CreateLogStream", "logs:PutLogEvents",
}


# ----------------------------------------------------------------- helpers
def norm(value):
    """Strip HCL quoting so every python-hcl2 version gives the same text.

    '"${aws_s3_bucket.data.arn}/raw/*"' and '${aws_s3_bucket.data.arn}' become
    'aws_s3_bucket.data.arn/raw/*' and 'aws_s3_bucket.data.arn'.
    """
    if isinstance(value, str):
        v = value.strip()
        if len(v) >= 2 and v[0] == v[-1] == '"':
            v = v[1:-1]
        return v.replace("${", "").replace("}", "")
    if isinstance(value, list):
        return [norm(v) for v in value]
    if isinstance(value, dict):
        return {norm(k): norm(v) for k, v in value.items() if not str(k).startswith("__")}
    return value


def as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


@pytest.fixture(scope="module")
def tf():
    """All infra/*.tf files merged: {"resource": {type: {name: body}}, "data": {...}}."""
    if hcl2 is None:
        return None
    merged: dict = {"resource": {}, "data": {}}
    for path in sorted(INFRA.glob("*.tf")):
        with path.open(encoding="utf-8") as f:
            parsed = norm(hcl2.load(f))
        for kind in ("resource", "data"):
            for block in parsed.get(kind, []):
                for typ, named in block.items():
                    merged[kind].setdefault(typ, {}).update(named)
    return merged


@pytest.fixture
def parsed(tf, request):
    if tf is None:
        if request.config.getoption("--lesson-strict"):
            pytest.fail("python-hcl2 is not installed: pip install python-hcl2")
        pytest.skip("python-hcl2 is not installed")
    return tf


def statements(tf) -> list[dict]:
    doc = tf["data"].get("aws_iam_policy_document", {}).get("ingest")
    assert doc is not None, 'data "aws_iam_policy_document" "ingest" is missing'
    return [dict(s) for s in as_list(doc.get("statement"))]


def conditions(stmt) -> list[tuple[str, str, frozenset]]:
    return [(c.get("test"), c.get("variable"), frozenset(as_list(c.get("values"))))
            for c in as_list(stmt.get("condition"))]


def grants(stmts, action):
    """(resource, conditions) pairs that allow `action`."""
    return [(r, conditions(s)) for s in stmts if action in as_list(s.get("actions"))
            for r in as_list(s.get("resources"))]


# ------------------------------------------------------ always-on sanity
def test_every_tf_file_parses(parsed):
    assert "aws_lambda_function" in parsed["resource"]
    assert set(parsed["resource"]["aws_scheduler_schedule"]) >= {"monitor"}


# --------------------------------------------------- lesson 5: IAM policy
@pytest.fixture
def ingest_policy(parsed, skip_if_lesson_pending):
    skip_if_lesson_pending(MAIN_TF, 5)
    return statements(parsed)


@pytest.mark.lesson5
def test_policy_is_still_attached_to_the_ingest_role(parsed, skip_if_lesson_pending):
    skip_if_lesson_pending(MAIN_TF, 5)
    attached = parsed["resource"]["aws_iam_role_policy"]["ingest"]
    assert attached["policy"] == "data.aws_iam_policy_document.ingest.json"
    assert attached["role"] == "aws_iam_role.ingest.id"


@pytest.mark.lesson5
def test_every_statement_is_a_plain_allow_with_a_sid(ingest_policy):
    for s in ingest_policy:
        assert s.get("sid"), f"statement without sid: {s}"
        assert s.get("effect", "Allow") == "Allow", "this policy only needs Allow statements"
        for key in ("not_actions", "not_resources", "principals", "not_principals"):
            assert key not in s, f"{s['sid']}: {key} is not needed here"
        assert as_list(s.get("actions")), f"{s['sid']}: no actions"
        assert as_list(s.get("resources")), f"{s['sid']}: no resources"


@pytest.mark.lesson5
def test_no_wildcard_or_unneeded_actions(ingest_policy):
    actions = {a for s in ingest_policy for a in as_list(s.get("actions"))}
    wild = sorted(a for a in actions if "*" in a)
    assert not wild, f"wildcard actions grant far more than ingest needs: {wild}"
    extra = sorted(actions - ALLOWED_ACTIONS)
    assert not extra, f"ingest.py never calls these, so the role must not have them: {extra}"


@pytest.mark.lesson5
def test_star_resource_only_for_putmetricdata_with_namespace_condition(ingest_policy):
    for s in ingest_policy:
        if "*" not in as_list(s.get("resources")):
            continue
        assert set(as_list(s.get("actions"))) == {"cloudwatch:PutMetricData"}, (
            f'{s["sid"]}: resources = ["*"] is only acceptable for PutMetricData')
        assert ("StringEquals", "cloudwatch:namespace", frozenset({"SD311"})) in conditions(s), (
            f'{s["sid"]}: restrict "*" with the cloudwatch:namespace condition (SD311)')


@pytest.mark.lesson5
def test_can_write_raw_and_quarantine_objects_only(ingest_policy):
    put = {r for r, _ in grants(ingest_policy, "s3:PutObject")}
    assert put == {f"{BUCKET}/raw/*", f"{BUCKET}/quarantine/*"}, (
        f"PutObject must cover exactly raw/* and quarantine/*, got {sorted(put)}")


@pytest.mark.lesson5
def test_can_read_raw_objects_only(ingest_policy):
    get = {r for r, _ in grants(ingest_policy, "s3:GetObject")}
    assert get == {f"{BUCKET}/raw/*"}, f"GetObject must cover exactly raw/*, got {sorted(get)}"


@pytest.mark.lesson5
def test_list_bucket_is_on_the_bucket_arn_and_limited_to_raw(ingest_policy):
    lst = grants(ingest_policy, "s3:ListBucket")
    assert lst, "previous_manifest() lists raw/: ListBucket is needed"
    for resource, conds in lst:
        assert resource == BUCKET, (
            f"ListBucket is a bucket-level action: use {BUCKET}, not {resource}")
        prefix = [c for c in conds if c[1] == "s3:prefix"]
        assert prefix, "limit ListBucket with a condition on s3:prefix"
        test, _, values = prefix[0]
        assert test == "StringLike" and values and values <= {"raw/*", "raw/"}, (
            f"the s3:prefix condition must be StringLike raw/*, got {test} {sorted(values)}")


@pytest.mark.lesson5
def test_can_read_project_ssm_parameters_only(ingest_policy):
    ssm = grants(ingest_policy, "ssm:GetParameters")
    assert ssm, "get_databricks_secrets() calls ssm:GetParameters"
    for s in ingest_policy:
        if any(a.startswith("ssm:") for a in as_list(s.get("actions"))):
            for r in as_list(s.get("resources")):
                assert r == "local.ssm_arn" or (":parameter/" in r and not r.endswith(
                    ":parameter/*")), f"SSM access must be limited to /sd311/*, got {r}"


@pytest.mark.lesson5
def test_can_publish_metrics_only_to_sd311(ingest_policy):
    cw = grants(ingest_policy, "cloudwatch:PutMetricData")
    assert cw, "put_metric() calls cloudwatch:PutMetricData"
    for _, conds in cw:
        assert ("StringEquals", "cloudwatch:namespace", frozenset({"SD311"})) in conds


# ------------------------------------------------ lesson 5: the schedule
@pytest.fixture
def ingest_schedule(parsed, skip_if_lesson_pending):
    skip_if_lesson_pending(MAIN_TF, 5)
    sched = parsed["resource"].get("aws_scheduler_schedule", {}).get("ingest")
    assert sched is not None, 'resource "aws_scheduler_schedule" "ingest" is missing'
    return sched


@pytest.mark.lesson5
def test_ingest_runs_daily_at_six_am(ingest_schedule):
    expr = " ".join(ingest_schedule["schedule_expression"].split())
    assert expr.startswith("cron(") and expr.endswith(")"), f"not a cron() expression: {expr}"
    fields = expr[5:-1].split(" ")
    assert len(fields) == 6, f"Scheduler cron has 6 fields (incl. year), got {fields}"
    minute, hour, dom, month, dow, year = fields
    assert (int(minute), int(hour)) == (0, 6), f"should fire at 06:00, got {hour}:{minute}"
    assert {dom, dow} == {"*", "?"} and month == "*" and year == "*", (
        f"should fire every day: got {expr}")


@pytest.mark.lesson5
def test_ingest_schedule_uses_pacific_time(ingest_schedule):
    assert ingest_schedule.get("schedule_expression_timezone") == "America/Los_Angeles", (
        "without a time zone the cron runs in UTC and drifts an hour at daylight saving")


@pytest.mark.lesson5
def test_ingest_schedule_target_and_no_retries(ingest_schedule):
    (window,) = as_list(ingest_schedule.get("flexible_time_window"))
    assert window.get("mode") == "OFF"
    (target,) = as_list(ingest_schedule.get("target"))
    assert target.get("arn") == "aws_lambda_function.ingest.arn"
    assert target.get("role_arn") == "aws_iam_role.scheduler.arn"
    retry = as_list(target.get("retry_policy"))
    assert retry and int(retry[0].get("maximum_retry_attempts")) == 0, (
        "set retry_policy { maximum_retry_attempts = 0 }")
