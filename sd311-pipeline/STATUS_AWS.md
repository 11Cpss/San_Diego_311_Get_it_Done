# AWS deployment evidence

Updated November 9, 2026 (Pacific time). The `sd311-main` stack is the current deployment. It runs in `us-east-2` and was created by Jaden through a separate Terraform backend.

## What is deployed

| Component | Current resource | Verification |
| --- | --- | --- |
| Terraform state | `s3://sd311-jadenw-tfstate/sd311-main/terraform.tfstate` | Versioning, public access blocking, and SSE-S3 checked in S3; post-deploy plan reported no changes |
| Source storage | `sd311-jadenw-data` | Live CSVs and manifests checked in the S3 console |
| Container image | `sd311-main-lambda:latest` in ECR | Built for `linux/amd64`, pushed, and visible in ECR |
| Ingest function | `sd311-main-ingest` | Active; 2048 MB, 300-second timeout, Databricks disabled |
| Daily trigger | `sd311-main-ingest-daily` | Enabled for 06:00 `America/Los_Angeles`; target is the new Lambda |
| Monitoring | Three `sd311-main-*` CloudWatch alarms → `sd311-main-alerts` SNS topic | Email subscription confirmed; direct SNS message and controlled data-quality alarm email received |
| Cost notification | `sd311-main-monthly` | $5 monthly budget deployed; an AWS Budget alerts but does not cap spending |

## Live ingest check

Jaden manually invoked `sd311-main-ingest` in the AWS console on November 9. Request ID: `67bf4814-3bf4-4607-a4f4-6b24a6b91f49`. The Lambda returned `status: ok`, and CloudWatch logs showed two downloads, successful Pandera validation, two S3 writes, `StaleSource=0` for both sources, and a successful completion.

| Source | Validated rows | CSV bytes | Stored receipt |
| --- | ---: | ---: | --- |
| Open | 85,338 | 31,279,228 | `raw/open/snapshot_date=2026-11-09/manifest.json` |
| Closed 2026 | 358,943 | 122,908,435 | `raw/closed_2026/snapshot_date=2026-11-09/manifest.json` |

The two CSV objects and two manifests were checked in the new S3 bucket, and the manifest row counts matched the Lambda logs. Total validated rows: **444,281**. Duration: **12.9 seconds**; maximum memory used: **822 MB**. The returned Databricks `run_id` was `null`, as expected with `SD311_DATABRICKS_ENABLED=false`.

## Alert check

The SNS subscription for the new `sd311-main-alerts` topic is confirmed. Jaden sent a labeled test message and received it. A separate, controlled `SetAlarmState` test moved `sd311-main-data-quality-failures` to `ALARM`; an AWS alarm email arrived. This verifies the CloudWatch alarm → SNS → email path. The test did not represent a bad city file or a real validation failure.

## Still to verify or build

- **Daily execution:** The schedule is enabled, but its first scheduled invocation at 06:00 Pacific on November 10 has not yet been observed. Check the Lambda log timestamp and new S3 snapshot after it fires.
- **Live bad-file path:** The quarantine path passed local integration tests; it has not been triggered by a bad live city CSV.
- **Databricks:** Disabled for this AWS-only deployment. No live Parquet upload, Databricks job, processing table, or model scoring is claimed.
- **GitHub automation:** OIDC and automatic deployment are disabled. The image and infrastructure were deployed manually. Lesson 9 workflow files are inactive templates.
- **Previous AWS stack:** The older `sd311` stack and schedule still exist as a fallback. After the new daily schedule succeeds, disable the older schedule and review that stack for retirement without deleting historical S3 data unexpectedly.

The local `infra/terraform.tfvars`, provider plugins, Terraform state, raw exports, and credentials are excluded from Git.
