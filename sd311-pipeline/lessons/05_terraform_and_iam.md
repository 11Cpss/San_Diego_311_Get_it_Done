# Lesson 5: Terraform and IAM (least privilege)

## What you'll build and why it matters

You write two blocks in `infra/main.tf`:

1. **The ingest Lambda's IAM policy**: `data "aws_iam_policy_document" "ingest"`. It lists exactly what the ingest code may do in AWS, and nothing more.
2. **The daily ingest schedule**: `resource "aws_scheduler_schedule" "ingest"`. It fires the Lambda at 06:00 San Diego time, every day, all year.

Why these two? "Least privilege" is the security question you'll get about any cloud project, and writing a policy by mapping every API call in your code to one statement is the skill behind it. The schedule is small, but it hides two classic bugs: UTC versus local time, and silent retries. The rest of `main.tf` (bucket, ECR, Lambdas, alarms, the GitHub deploy role) is given and you read it.

## Concept

**Terraform** turns "click around the AWS console" into code you can review. You *declare* what should exist. `terraform plan` shows the difference between that and reality, and `terraform apply` makes reality match. The **state file** is Terraform's memory of which real resource belongs to which block. It lives in S3 (`backend.tf`) and is never committed.

**IAM** answers "who can do what to which thing". Every AWS API call is checked against the caller's policies. A policy is a list of **statements**, and each statement has:

- `actions`: API calls, like `s3:PutObject`;
- `resources`: the ARNs those calls may touch, like `arn:aws:s3:::bucket/raw/*`;
- optional `condition` blocks that narrow it further, like "only when the `s3:prefix` is `raw/*`".

**Least privilege** means each identity gets only the actions and resources it really uses, so a bug or a leaked credential can do limited damage. Two details catch everyone:

- **Bucket actions versus object actions.** `s3:ListBucket` acts on the *bucket* ARN (`arn:aws:s3:::name`); `s3:GetObject`/`PutObject` act on *object* ARNs (`arn:aws:s3:::name/key`). Putting `ListBucket` on `name/*` silently grants nothing.
- **Actions with no resource ARNs.** `cloudwatch:PutMetricData` can't be scoped to a resource, so its resource must be `"*"`. You restrict it with a **condition key** instead (`cloudwatch:namespace`).

Three kinds of identity exist here: **Lambda roles** (assumed by `lambda.amazonaws.com`, tight, one per function), the **Scheduler role** (may only invoke our two Lambdas) and the **GitHub deploy role** (assumed through OIDC by *this repo's* `prod` environment; broader, because it runs Terraform).

**EventBridge Scheduler** runs a target on a schedule. Its `cron()` has **six** fields: minutes, hours, day-of-month, month, day-of-week, year. One of day-of-month and day-of-week must be `?`. Unlike classic EventBridge rules (UTC only), it takes a time zone, so 06:00 stays 06:00 through daylight saving. By default it **retries** a failed delivery up to 185 times over 24 hours.

## How it fits the pipeline

- `terraform apply` (by hand the first time, then from `deploy.yml`) creates the role and attaches your policy with `aws_iam_role_policy.ingest`.
- At runtime, every boto3 call in `src/sd311/ingest.py` is checked against your policy: `s3.put_object` (raw, quarantine), `s3.get_object` and the `list_objects_v2` paginator (`previous_manifest`), `ssm.get_parameters` (`get_databricks_secrets`), and `cloudwatch.put_metric_data` (`put_metric`). Lambda writes its own logs with `logs:CreateLogStream`/`PutLogEvents`.
- **A missing permission** gives an `AccessDenied` exception, the Lambda fails, the `ingest-errors` alarm emails you, and no data lands that day.
- **An extra permission** breaks nothing today. That's why it's dangerous: nobody notices until a bug, or an attacker with the role, deletes `backfill/`.
- **A schedule without a time zone** runs at 06:00 UTC, which is 22:00 or 23:00 the evening before in San Diego, before the city publishes. Every day would look stale.
- **A schedule with default retries** re-runs a failed ingest for up to a day: the same downloads, metrics and possibly another Databricks run. We alert and fix by hand instead.

## Code walkthrough

Open these three files:

1. **`infra/main.tf`**, top to bottom. It's one root module in eight numbered sections. Read the **monitor** role policy in section 4 carefully: it's a complete example of the syntax you'll write. Each statement has a `sid` (a label for humans), `actions`, `resources` and maybe a `condition`:
   ```hcl
   statement {
     sid       = "PublishMetrics"
     actions   = ["cloudwatch:PutMetricData"]
     resources = ["*"]                         # no resource ARNs exist for this action
     condition {
       test     = "StringEquals"
       variable = "cloudwatch:namespace"
       values   = ["SD311"]
     }
   }
   ```
   Other things worth noticing:
   - `local.ssm_arn` covers every parameter under `/sd311/`.
   - The SSM parameters have `lifecycle { ignore_changes = [value] }`: Terraform creates the slot with a placeholder, you set the real secret with the CLI, and Terraform never overwrites it. The secret never appears in a `.tf` file.
   - `aws_lambda_function_event_invoke_config.ingest` turns off Lambda's own async retries.
   - The GitHub trust policy pins `sub = repo:<owner>/<repo>:environment:prod`. Without that line, *any* GitHub repository could assume your role.
   - Each alarm's `treat_missing_data` is a design decision (see `docs/runbook.md`).
2. **`infra/versions.tf`**: the provider version pins.
3. **`infra/backend.tf`**: the empty S3 backend that `terraform init -backend-config=...` fills in.

## Your task

```bash
pip install python-hcl2                # the checker parses HCL; no terraform binary needed
python scripts/lessons.py check 5      # fails until you're done
```

The stub keeps a `WriteOwnLogs` statement as a model and lists TODO 1–5 in comments.

**Piece 1: the ingest policy.** Add one statement per need, each with a `sid`:

| need (code that calls it) | action(s) | resource | condition |
|---|---|---|---|
| land raw files and quarantine bad ones | `s3:PutObject` | `${aws_s3_bucket.data.arn}/raw/*` and `.../quarantine/*` | – |
| read yesterday's manifest | `s3:GetObject` | `.../raw/*` | – |
| list manifests | `s3:ListBucket` | `aws_s3_bucket.data.arn` (the bucket itself) | `StringLike` on `s3:prefix` = `raw/*` |
| Databricks secrets | `ssm:GetParameters` (and `ssm:GetParameter` if you like) | `local.ssm_arn` | – |
| metrics | `cloudwatch:PutMetricData` | `"*"` | `StringEquals` on `cloudwatch:namespace` = `SD311` |

Rules the checker enforces: no wildcard actions (`s3:*`, `*`), no `"*"` resource except for `PutMetricData`, no action the code doesn't call, and `Allow` statements only.

**Piece 2: the ingest schedule.** Write `resource "aws_scheduler_schedule" "ingest"` above the monitor schedule. It fires at 06:00, every day, in `America/Los_Angeles`, with `flexible_time_window { mode = "OFF" }`. The target is `aws_lambda_function.ingest.arn`, it uses `aws_iam_role.scheduler.arn` and has input `jsonencode({})`, and it has `retry_policy { maximum_retry_attempts = 0 }`.

Keep the file valid HCL. Align the `=` signs the way the rest of the file does, because CI's `terraform fmt -check` will flag anything else.

**Done when** `python scripts/lessons.py check 5` prints `PASS` (12 tests). If you're stuck, `python scripts/lessons.py diff 5` shows the answer key next to your file, and `solve 5 --yes` puts it in place (your version is backed up first).

## Own it

1. Why does `ListBucket` go on the bucket ARN while `PutObject` goes on `bucket/*`? What would `ListBucket` on `bucket/*` actually allow?
2. The monitor role has no S3 statement at all. Why is that correct, and what error would you see if someone later made the monitor read S3?
3. There are two retry settings set to 0: one on the schedule's target and one in `aws_lambda_function_event_invoke_config`. What does each one prevent?
4. What stops a random GitHub repository from assuming `sd311-github-deploy`? Point to the exact line.

**Change one thing and predict.** Delete the `schedule_expression_timezone` line from your schedule. Before running the checker, write down the San Diego clock time the ingest would run in January and in July, and which `snapshot_date` it would stamp on the files (`today_local` uses Pacific time). Then run `check 5` and read the failure message. Put the line back.

## When you have AWS credentials (optional, later)

Use an IAM user or SSO profile, **never root**:

1. `aws s3 mb s3://<you>-sd311-tfstate --region us-west-2`
2. `cp infra/terraform.tfvars.example infra/terraform.tfvars` and fill it in.
3. `cd infra && terraform init -backend-config="bucket=<you>-sd311-tfstate" -backend-config="key=sd311/terraform.tfstate" -backend-config="region=us-west-2"`
4. `terraform fmt -check && terraform validate`. This is the first real Terraform check of this code; the sandbox it was written in couldn't download Terraform.
5. `terraform plan` and read it. How many resources? Which ones cost money? Don't apply yet: the Lambdas need an image in ECR first (see the README's "Deploy").

## Tradeoffs

| Choice | Upside | Downside |
|---|---|---|
| One root module | easy to read top to bottom | doesn't scale to many environments |
| Inline role policies | the policy sits next to its role | not reusable across roles |
| Broad-ish deploy role | Terraform can manage everything | a compromised `main` is powerful, so protect the `prod` environment with reviewers |
| SSE-S3 instead of KMS | free, zero config | no key-level audit or rotation control |
| No retries on ingest | no duplicate runs; failures are visible | a one-off network blip costs a day until someone re-runs it |
| Budgets | early warning | they only notify; they don't stop spending |

## Interview questions

1. What is Terraform state, and why keep it remote?
2. How do you keep secrets out of Terraform?
3. What is OIDC federation for CI?
4. Give a least-privilege example from your project.
5. What's the difference between `plan` and `apply`?
6. What's the difference between a role's trust policy and its permissions policy?
7. Why EventBridge Scheduler instead of an EventBridge rule for a daily job?

## Time

| part | working | minutes |
|---|---|---|
| Read | about 1,600 words ÷ 150 wpm = 11, plus 3 files × 5 = 15 | 26 |
| Build | IAM policy (medium, 30) + schedule (small, 15) | 45 |
| Check and explain | 2 pieces × 10 + 10 for Own it | 30 |
| **Total** | 26 + 45 + 30 = 101, rounded to the nearest 5 | **100** |

## Answers

1. **State:** the mapping from your code to real resource IDs. Keep it remote (S3) so CI and you share one copy and it is never lost or committed. It can contain sensitive values.
2. **Secrets:** create the SSM parameter with a placeholder and `ignore_changes = [value]`, set the real value out of band with the CLI, and read it at runtime.
3. **OIDC:** GitHub issues a short-lived signed token for the job. AWS trusts GitHub's issuer and exchanges the token for temporary credentials, so no long-lived keys are stored in GitHub. The trust policy pins the repo and environment.
4. **Least privilege:** the ingest role can only PutObject under `raw/` and `quarantine/`, read and list `raw/`, read `/sd311/*` parameters, and publish metrics to the `SD311` namespace. It cannot delete anything or touch `backfill/`.
5. **plan vs apply:** plan is a dry-run diff; apply executes it. A mature setup runs plan on every PR for review and applies only on `main`.
6. **Trust vs permissions:** the trust policy (`assume_role_policy`) says *who* may become the role, for example `lambda.amazonaws.com`. The permissions policy says *what* the role may do once assumed.
7. **Scheduler:** it supports time zones (06:00 stays 06:00 through daylight saving), per-target retry policies and a flexible time window. Classic rules are UTC-only.
