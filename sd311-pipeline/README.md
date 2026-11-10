# sd311-pipeline

A daily data + ML pipeline over San Diego's **Get It Done** (311) service
requests, built as a portfolio project. AWS handles ingestion, storage, and
monitoring. The Databricks code is the next phase for transformation (bronze →
silver → gold with dbt) and daily scoring. Terraform and Docker deploy the AWS
part; the GitHub Actions files are unfinished Lesson 9 templates.

> Status: `sd311-main` runs the AWS-only ingest path in `us-east-2`. A manual
> run, S3 outputs, and alert delivery were verified. The first scheduled run
> and Databricks processing are still pending. See `STATUS_AWS.md` for evidence.

## Architecture

```
 06:00 PT  EventBridge Scheduler ──► Lambda sd311-main-ingest (container image)
              │  GET seshat.datasd.org/get_it_done_reports/*.csv (retries, browser UA)
              │  sha256 + rows ─► pandera schema
              │     ├─ fail ─► s3://bucket/quarantine/...  + DataQualityFailures metric, STOP
              │     └─ ok   ─► s3://bucket/raw/<source>/snapshot_date=D/<file>.csv + manifest.json
              │                StaleSource metric (sha == yesterday's?)
              │                [future, when Databricks is enabled]
              │                Parquet (all strings + snapshot_date, source)
              │                ─► Databricks Files API PUT /Volumes/sd311/bronze/landing/<source>/snapshot_date=D/
              └─► Databricks Jobs API run-now (job_id from SSM)
                       Databricks job: COPY INTO bronze → dbt silver/gold → score → run_summary.json
 [future] 08:00 PT  EventBridge Scheduler ──► Lambda sd311-monitor
              GET /Volumes/sd311/ml/run_summaries/date=D/run_summary.json
              ─► CloudWatch metrics (namespace SD311) ─► 6 alarms ─► SNS email
```

When enabled, Databricks secrets (host/token/job id) will live in SSM Parameter
Store SecureStrings under `/${var.project}/databricks/*`. The JSON contract between the two halves
is `docs/run_summary_schema.md`. Why this split: `docs/adr/0001-aws-plus-databricks.md`.

## Folder map

| path | what |
|---|---|
| `src/sd311/` | Python package: `config`, `logging_utils`, `schemas`, `ingest`, `monitor` |
| `ml/` | the closure-time model — see [`ml/README.md`](ml/README.md) |
| `ml/sd311_core/` | model package (io, cleaning, labels, features, baseline, metrics, train, backtest) plus `serving`, `monitoring`, `registry` used by the Databricks ML tasks |
| `ml/scripts/`, `ml/analysis/` | `make_slim`, `build_dataset`, `run_backtest`, `make_figures`, `fit_final`; offline analyses |
| `ml/tests/` | model tests (labels, leakage, serving, monitoring, MLflow registry); many are lesson checkers |
| `ml/reports/`, `ml/models/` | `RESULTS.md`, figures, backtest JSON; `model.cbm` + `metadata.json` |
| `tests/unit/`, `tests/integration/` | pytest; integration uses moto + responses (no cloud needed) |
| `tests/fixtures/open_sample.csv` | 1,000 sampled real rows (address/description synthetic) |
| `docker/` | Lambda container image (one image, two handlers) |
| `scripts/lessons.py`, `lessons/_kit/` | lesson system: status / check / diff / solve / reset; stubs and answer keys |
| `infra/` | Terraform (S3, ECR, Lambdas, IAM, Scheduler, SSM, SNS, alarms, Budgets, GitHub OIDC) |
| `.github/workflows/` | Inactive Lesson 9 CI/deploy templates; the project is a subfolder of the parent GitHub repository |
| `scripts/day_one_check.py` | smoke test of Databricks + city access with a PASS/FAIL table |
| `scripts/backfill.py` | one-time upload of historical yearly CSVs (`--dry-run`) |
| `docs/` | runbook (one section per alarm), run_summary schema, ADRs 0001–0003 |
| `lessons/` | learning notes: start with `lessons/00_START_HERE.md` |
| `databricks/` | Databricks Asset Bundle: jobs and tasks — see [`databricks/README.md`](databricks/README.md) |
| `dbt/` | dbt project (DuckDB locally, Databricks in prod) — see [`dbt/README.md`](dbt/README.md) |

## Lessons

| # | lesson | side |
|---|---|---|
| 01 | [Why the old numbers were wrong](lessons/01_why_the_old_numbers_were_wrong.md) | model |
| 02 | [Labels and as-of features](lessons/02_labels_and_as_of_features.md) | model |
| 03 | [Baselines, metrics and backtesting](lessons/03_baselines_metrics_backtesting.md) | model |
| 04 | [Ingest: Lambda, Docker, S3](lessons/04_ingest_lambda_docker_s3.md) | AWS |
| 05 | [Terraform and IAM](lessons/05_terraform_and_iam.md) | AWS |
| 06 | [Lakehouse and Delta MERGE](lessons/06_lakehouse_and_delta_merge.md) | Databricks |
| 07 | [dbt models and tests](lessons/07_dbt_models_and_tests.md) | Databricks |
| 08 | [MLflow, daily scoring and monitoring](lessons/08_mlflow_scoring_monitoring.md) | Databricks + model |
| 09 | [CI/CD](lessons/09_ci_cd.md) | AWS |

## Databricks and dbt

The Databricks half is documented separately:
- [`databricks/README.md`](databricks/README.md) — bundle, daily job, bronze loader, ML tasks
- [`dbt/README.md`](dbt/README.md) — models, tests, running locally on DuckDB

## Quickstart (local, no cloud account needed)

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # 3.11+ works
pip install -e ".[dev,ml]"      # sd311 (src/) + sd311_core (ml/) + catboost/mlflow
pip install -r databricks/requirements-dev.txt   # duckdb, pyyaml (+ pyspark, needs Java 17)
ruff check .
pytest                          # tests/ + ml/tests + databricks/tests (unfinished lesson stubs are skipped)
python scripts/lessons.py status  # lesson progress; `check N` is the strict checker for lesson N
python scripts/day_one_check.py --help
python scripts/backfill.py --dir ~/data/raw --bucket demo --dry-run
docker build -f docker/Dockerfile -t sd311-lambda .   # needs Docker
```

Run the image locally (Lambda runtime emulator is built into the base image):

```bash
docker run --rm -p 9000:8080 -e SD311_BUCKET=x -e SD311_DATABRICKS_ENABLED=false \
  sd311-lambda   # then: curl -d '{}' http://localhost:9000/2015-03-31/functions/function/invocations
```
(Without AWS credentials the S3 write will fail — that's expected.)

## Deploy (manual, once you have credentials)

Prereqs: AWS account with a non-root administrator or SSO profile, AWS CLI,
Terraform ≥ 1.6, and Docker. A Databricks workspace is needed only when
`enable_databricks = true`.

For an **AWS-only first deploy**, copy `infra/terraform.tfvars.example` and fill
in a globally unique `bucket_name` and your `alert_email`. Keep
`enable_databricks = false` and `enable_github_deploy = false`. This deploys
ingest, S3, its daily schedule, ingest alarms, SNS, and the budget; it omits
Databricks parameters, the monitor, model alarms, and the GitHub deploy role.
The Lambda receives `SD311_DATABRICKS_ENABLED=false`, so it stores validated
CSVs in S3 without contacting Databricks. Use the AWS Region assigned to your
account or project in both `region` and the commands below; an account restricted
to another Region cannot sign in with `us-west-2`. Sign in locally with `aws
login --region <your-region>`, then use the sequence below, skipping step 5. On an
Apple Silicon Mac, build the image for Lambda's configured x86_64 architecture:
`docker buildx build --platform linux/amd64 --provenance=false --load -f ../docker/Dockerfile -t "$REPO:latest" ..`.

```bash
export AWS_REGION=us-east-2 # replace with your account/project's supported Region
# 1. State bucket (once)
aws s3 mb s3://<you>-sd311-tfstate --region "$AWS_REGION"
# 2. Variables
cp infra/terraform.tfvars.example infra/terraform.tfvars   # edit it
cd infra
terraform init -backend-config="bucket=<you>-sd311-tfstate" \
  -backend-config="key=sd311/terraform.tfstate" -backend-config="region=$AWS_REGION"
# 3. ECR first (Lambdas need an image to exist)
terraform apply -target=aws_ecr_repository.lambda
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}"
docker buildx build --platform linux/amd64 --provenance=false --load -f ../docker/Dockerfile -t "$REPO:latest" .. && docker push "$REPO:latest"
# 4. Everything else
terraform apply
# 5. Real secrets (Terraform created placeholders and ignores their values)
aws ssm put-parameter --overwrite --type SecureString --name /sd311/databricks/host  --value https://dbc-xxxx.cloud.databricks.com
aws ssm put-parameter --overwrite --type SecureString --name /sd311/databricks/token --value dapi...
aws ssm put-parameter --overwrite --type SecureString --name /sd311/databricks/daily_job_id --value 123
# 6. Confirm the SNS subscription email; smoke test
aws lambda invoke --function-name sd311-main-ingest --payload '{"trigger_job":false}' \
  --cli-binary-format raw-in-base64-out out.json && cat out.json
```

For a later GitHub deployment setup, move these workflow files to the parent
repository's root `.github/workflows/` directory and update their working
directories and AWS Region. Complete Lesson 9 and remove the workflow guards
before expecting any automatic deployment. Then create a `prod` environment with variables
`AWS_DEPLOY_ROLE_ARN` (`terraform output github_deploy_role_arn`),
`TF_STATE_BUCKET`, `SD311_BUCKET_NAME`, `ALERT_EMAIL`,
`DATABRICKS_WAREHOUSE_ID` and `DATABRICKS_NOTIFICATION_EMAIL`, and secrets
`DATABRICKS_HOST`, `DATABRICKS_TOKEN`. Once the workflow is completed and
enabled, pushes to `main` can deploy.
The deploy job passes the two Databricks values to the bundle as
`BUNDLE_VAR_warehouse_id` and `BUNDLE_VAR_notification_email` (the bundle
variables have no defaults, so the deploy fails without them).

## Known limitations

- **AWS-only first deployment.** The Lambda and S3 ingest path were verified
  live on 2026-11-09. Databricks upload, job execution, and the first scheduled
  invocation still need live verification; see `STATUS_AWS.md`.
- **Whole files every day.** The open file is re-downloaded in full; fine at
  ~100 MB, wasteful at 10×. The ingest Lambda has 2048 MB because the real
  files include description text; very large closed
  files may need more memory or chunked Parquet writing.
- **One bad file blocks the day.** Any quarantined file stops the run (no
  partial loads). Simple and safe, but the open file can be held hostage by a
  bad closed file.
- **Future deploy role is broad** (Terraform needs to manage IAM, Lambda, etc.).
  GitHub OIDC is disabled in the current deployment. Protect the `prod`
  environment with required reviewers if it is enabled later.
- **Freshness is byte-level.** A city re-export with a changed timestamp
  column looks "fresh" even if no cases changed.
- **City server access:** the site may block cloud IPs or non-browser
  clients; we send a browser-like User-Agent and retry, but cannot fix a block.
- **Cost guardrail:** The $5 monthly AWS Budget sends notifications; it does not
  stop spending. Custom metrics, alarms, storage, and requests can incur charges.
