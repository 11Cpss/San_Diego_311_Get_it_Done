###############################################################################
# sd311 AWS infrastructure (one root module, read top to bottom)
#
#   1. Storage      S3 bucket (versioned, private, encrypted)
#   2. Images       ECR repository for the Lambda container image
#   3. Secrets      SSM SecureString parameters for Databricks
#   4. Lambdas      ingest + monitor, their IAM roles and log groups
#   5. Schedules    EventBridge Scheduler: 06:00 ingest, 08:00 monitor (Pacific)
#   6. Alerts       SNS topic + 6 CloudWatch alarms
#   7. Cost         AWS Budgets ($1 and $5 notifications)
#   8. Deploys      GitHub OIDC provider + deploy role
###############################################################################

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  image_uri  = "${aws_ecr_repository.lambda.repository_url}:${var.image_tag}"
  ssm_prefix = "/${var.project}/databricks"
  # ARN pattern covering every parameter under /sd311/
  ssm_arn = "arn:aws:ssm:${var.region}:${local.account_id}:parameter/${var.project}/*"
}

# -----------------------------------------------------------------------------
# 1. Storage
# -----------------------------------------------------------------------------
resource "aws_s3_bucket" "data" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256" # SSE-S3: free, no KMS key to manage
    }
  }
}

# Old object versions cost money; keep them 30 days then expire.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# -----------------------------------------------------------------------------
# 2. Images
# -----------------------------------------------------------------------------
resource "aws_ecr_repository" "lambda" {
  name                 = "${var.project}-lambda"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

# Keep only the 10 newest images (ECR storage is billed per GB).
resource "aws_ecr_lifecycle_policy" "lambda" {
  repository = aws_ecr_repository.lambda.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# -----------------------------------------------------------------------------
# 3. Secrets (values are set by hand, Terraform only creates the slots)
#
#   aws ssm put-parameter --name /sd311/databricks/token --type SecureString \
#       --value 'dapi...' --overwrite
# -----------------------------------------------------------------------------
resource "aws_ssm_parameter" "databricks" {
  for_each = var.enable_databricks ? toset(["host", "token", "daily_job_id"]) : toset([])

  name  = "${local.ssm_prefix}/${each.key}"
  type  = "SecureString"
  value = "CHANGE_ME"

  lifecycle {
    # After you overwrite the placeholder, Terraform must not put it back.
    ignore_changes = [value]
  }
}

# -----------------------------------------------------------------------------
# 4. Lambdas
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${var.project}-ingest"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "monitor" {
  count             = var.enable_databricks ? 1 : 0
  name              = "/aws/lambda/${var.project}-monitor"
  retention_in_days = var.log_retention_days
}

# --- ingest role: write raw/ + quarantine/, read raw/ (previous manifest) ---
resource "aws_iam_role" "ingest" {
  name               = "${var.project}-ingest-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# LESSON 5 STUB: you write the ingest policy document (lessons/05_terraform_and_iam.md).
#
# What the ingest Lambda does at runtime (src/sd311/ingest.py), and so what it
# needs permission for, one statement each (give every statement a `sid`):
#   TODO 1  write the untouched CSV + manifest.json under raw/, and bad files
#           under quarantine/ (s3_raw / quarantine steps). Nothing else in the
#           bucket: not backfill/, not the bucket root.
#   TODO 2  read objects under raw/ (previous_manifest() reads yesterday's manifest).
#   TODO 3  list keys, but only under raw/ (previous_manifest() lists them).
#           Watch out: listing is an action on the BUCKET ARN, not on bucket/*;
#           narrow it with a condition on the `s3:prefix` key.
#   TODO 4  read the SSM parameters under /sd311/ (get_databricks_secrets uses
#           get_parameters). local.ssm_arn already holds the right ARN pattern.
#   TODO 5  publish CloudWatch metrics, but only into the "SD311" namespace.
#           This action has no resource ARNs, so `resources = ["*"]` is the one
#           place a "*" is allowed; restrict it with the `cloudwatch:namespace`
#           condition key instead.
# Rules: no wildcard actions (no "s3:*", no "*"), no "*" resource outside TODO 5,
# no Delete actions. The monitor policy below is a complete example of the syntax.
# Check: python scripts/lessons.py check 5
data "aws_iam_policy_document" "ingest" {
  # Given: CloudWatch Logs for this function's own log group only.
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }

  statement {
    sid     = "WriteRawAndQuarantine"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.data.arn}/raw/*",
    "${aws_s3_bucket.data.arn}/quarantine/*", ]
  }
  statement {
    sid       = "ReadRawFiles"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/raw/*"]
  }

  statement {
    sid       = "ListRawFiles"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["raw/*"]
    }

  }
  statement {
    sid       = "ReadDatabricksSecrets"
    actions   = ["ssm:GetParameters"]
    resources = [local.ssm_arn]
  }
  statement {
    sid       = "PublishMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["SD311"]
    }
  }


}



resource "aws_iam_role_policy" "ingest" {
  name   = "ingest-least-privilege"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

# --- monitor role: no S3 at all ---
resource "aws_iam_role" "monitor" {
  count              = var.enable_databricks ? 1 : 0
  name               = "${var.project}-monitor-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "monitor" {
  count = var.enable_databricks ? 1 : 0
  statement {
    sid       = "ReadDatabricksSecrets"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [local.ssm_arn]
  }
  statement {
    sid       = "PublishMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["SD311"]
    }
  }
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.monitor[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "monitor" {
  count  = var.enable_databricks ? 1 : 0
  name   = "monitor-least-privilege"
  role   = aws_iam_role.monitor[0].id
  policy = data.aws_iam_policy_document.monitor[0].json
}

# --- the functions: same image, different handler via image_config.command ---
locals {
  lambda_env = {
    SD311_BUCKET             = aws_s3_bucket.data.bucket
    SD311_SSM_PREFIX         = local.ssm_prefix
    SD311_METRIC_NAMESPACE   = "SD311"
    SD311_TIMEZONE           = "America/Los_Angeles"
    SD311_DATABRICKS_ENABLED = tostring(var.enable_databricks)
  }
}

resource "aws_lambda_function" "ingest" {
  function_name = "${var.project}-ingest"
  role          = aws_iam_role.ingest.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  memory_size   = 2048 # real files include description text; pandas + CSV + Parquet copy need headroom
  timeout       = 300
  architectures = ["x86_64"]

  image_config {
    command = ["sd311.ingest.handler"]
  }

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.ingest, aws_iam_role_policy.ingest]
}

resource "aws_lambda_function" "monitor" {
  count         = var.enable_databricks ? 1 : 0
  function_name = "${var.project}-monitor"
  role          = aws_iam_role.monitor[0].arn
  package_type  = "Image"
  image_uri     = local.image_uri
  memory_size   = 512
  timeout       = 60
  architectures = ["x86_64"]

  image_config {
    command = ["sd311.monitor.handler"]
  }

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.monitor, aws_iam_role_policy.monitor]
}

# Scheduled runs are async; a failed validation is reported via metrics, so
# don't let Lambda silently re-run the whole download twice more.
resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name          = aws_lambda_function.ingest.function_name
  maximum_retry_attempts = 0
}

# -----------------------------------------------------------------------------
# 5. Schedules (EventBridge Scheduler supports time zones natively, so
#    06:00 stays 06:00 across daylight-saving changes)
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.project}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "invoke-sd311-lambdas"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = concat([aws_lambda_function.ingest.arn], aws_lambda_function.monitor[*].arn)
    }]
  })
}

# LESSON 5 STUB: you write the ingest schedule (resource "aws_scheduler_schedule" "ingest").
#
#   TODO  run the ingest Lambda every day at 06:00 San Diego time, all year:
#         - an EventBridge Scheduler cron() expression has SIX fields
#           (minutes hours day-of-month month day-of-week year), and one of
#           day-of-month / day-of-week must be "?";
#         - set the time zone so 06:00 stays 06:00 across daylight saving time
#           (an IANA name, like the monitor schedule below);
#         - no flexible time window;
#         - target: the ingest function, invoked with the scheduler role, input "{}";
#         - retry_policy with maximum_retry_attempts = 0: a failed ingest must not
#           re-download and re-land the files on its own (we alert instead).
# The monitor schedule right below is a working example; yours differs in the
# hour, the target and the retry policy.

resource "aws_scheduler_schedule" "ingest" {
  name                         = "${var.project}-ingest-daily"
  schedule_expression          = "cron(0 6 * * ? *)"
  schedule_expression_timezone = "America/Los_Angeles"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ingest.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}
resource "aws_scheduler_schedule" "monitor" {
  count                        = var.enable_databricks ? 1 : 0
  name                         = "${var.project}-monitor-daily"
  schedule_expression          = "cron(0 8 * * ? *)"
  schedule_expression_timezone = "America/Los_Angeles"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.monitor[0].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})
  }
}

# -----------------------------------------------------------------------------
# 6. Alerts. Each alarm has a section in docs/runbook.md.
#    Daily jobs -> 1-day periods. treat_missing_data says what "no data" means.
# -----------------------------------------------------------------------------
resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email # AWS emails a confirmation link; click it!
}

locals {
  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "ingest_errors" {
  alarm_name          = "${var.project}-ingest-errors"
  alarm_description   = "Ingest Lambda raised an error. Runbook: docs/runbook.md#ingest-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.ingest.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching" # no invocations = no errors
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "data_quality" {
  alarm_name          = "${var.project}-data-quality-failures"
  alarm_description   = "A file failed schema checks (ingest) or Databricks DQ checks. Runbook: docs/runbook.md#data-quality-failures"
  namespace           = "SD311"
  metric_name         = "DataQualityFailures"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching" # metric is only sent when something is checked
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "stale_source" {
  alarm_name          = "${var.project}-stale-source-2d"
  alarm_description   = "Open-requests file unchanged for 2 consecutive days. Runbook: docs/runbook.md#stale-source"
  namespace           = "SD311"
  metric_name         = "StaleSource"
  dimensions          = { Source = "open" }
  statistic           = "Maximum"
  period              = 86400
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching" # ingest not running is caught by JobMissing/Errors
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "job_missing" {
  count               = var.enable_databricks ? 1 : 0
  alarm_name          = "${var.project}-databricks-job-missing"
  alarm_description   = "No run_summary.json for today (or monitor did not run). Runbook: docs/runbook.md#job-missing"
  namespace           = "SD311"
  metric_name         = "JobMissing"
  statistic           = "Maximum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "breaching" # silence means monitor itself is broken
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "low_f1" {
  count               = var.enable_databricks ? 1 : 0
  alarm_name          = "${var.project}-live-macro-f1-low"
  alarm_description   = "Live macro-F1 below ${var.f1_alarm_threshold}. Runbook: docs/runbook.md#live-macro-f1-low"
  namespace           = "SD311"
  metric_name         = "LiveMacroF1"
  statistic           = "Minimum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = var.f1_alarm_threshold
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching" # F1 needs closed labels; may be absent some days
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "drift" {
  count               = var.enable_databricks ? 1 : 0
  alarm_name          = "${var.project}-max-psi-high"
  alarm_description   = "Feature drift: max PSI above ${var.psi_alarm_threshold}. Runbook: docs/runbook.md#max-psi-high"
  namespace           = "SD311"
  metric_name         = "MaxPSI"
  statistic           = "Maximum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = var.psi_alarm_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# -----------------------------------------------------------------------------
# 7. Cost guardrail. Budgets only NOTIFY; they do not stop spending.
# -----------------------------------------------------------------------------
resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = "5"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 1
    threshold_type             = "ABSOLUTE_VALUE" # $1 actual spend
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [coalesce(var.budget_email, var.alert_email)]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 5
    threshold_type             = "ABSOLUTE_VALUE" # $5 forecasted
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [coalesce(var.budget_email, var.alert_email)]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 5
    threshold_type             = "ABSOLUTE_VALUE" # $5 actual
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [coalesce(var.budget_email, var.alert_email)]
  }
}

# -----------------------------------------------------------------------------
# 8. GitHub Actions deploys via OIDC: no long-lived AWS keys in GitHub.
# -----------------------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.enable_github_deploy && var.create_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS no longer checks these for GitHub, but older provider versions require the field.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

locals {
  github_oidc_existing_arn = "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"
  github_oidc_arn          = var.enable_github_deploy && var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : local.github_oidc_existing_arn
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only jobs in THIS repo running in the `prod` environment (deploy.yml).
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:environment:prod"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count                = var.enable_github_deploy ? 1 : 0
  name                 = "${var.project}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.github_assume.json
  max_session_duration = 3600
}

# Terraform needs to manage everything above, so this role is broader than the
# Lambda roles. It is still scoped by service and, where possible, by name.
data "aws_iam_policy_document" "github_deploy" {
  count = var.enable_github_deploy ? 1 : 0
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "EcrManageAndPushOwnRepo"
    actions   = ["ecr:*"]
    resources = [aws_ecr_repository.lambda.arn]
  }
  statement {
    sid     = "ManageProjectLambdas"
    actions = ["lambda:*"]
    resources = [
      "arn:aws:lambda:${var.region}:${local.account_id}:function:${var.project}-*",
    ]
  }
  statement {
    sid     = "ManageProjectRoles"
    actions = ["iam:*"]
    resources = [
      "arn:aws:iam::${local.account_id}:role/${var.project}-*",
      "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com",
    ]
  }
  statement {
    sid     = "ManageProjectBucketAndState"
    actions = ["s3:*"]
    resources = [
      "arn:aws:s3:::${var.bucket_name}",
      "arn:aws:s3:::${var.bucket_name}/*",
      "arn:aws:s3:::*tfstate*", # name your state bucket <something>-tfstate
      "arn:aws:s3:::*tfstate*/*",
    ]
  }
  statement {
    sid       = "ManageProjectParams"
    actions   = ["ssm:*"]
    resources = [local.ssm_arn]
  }
  statement {
    sid = "ServicesWithoutUsefulResourceScoping"
    actions = [
      "ssm:DescribeParameters", "logs:*", "scheduler:*", "sns:*", "cloudwatch:*",
      "budgets:*", "ecr:DescribeRepositories", "lambda:GetAccountSettings",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "PassOnlyProjectRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/${var.project}-*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count  = var.enable_github_deploy ? 1 : 0
  name   = "deploy-sd311"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy[0].json
}
