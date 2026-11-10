variable "region" {
  description = "AWS region for everything."
  type        = string
  default     = "us-west-2"
}

variable "project" {
  description = "Name prefix for resources."
  type        = string
  default     = "sd311"
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name for raw/quarantine/backfill data."
  type        = string
}

variable "image_tag" {
  description = "Tag of the Lambda image in ECR (CI passes the git SHA)."
  type        = string
  default     = "latest"
}

variable "alert_email" {
  description = "Email that receives alarm notifications (confirm the SNS email!)."
  type        = string
}

variable "budget_email" {
  description = "Email for AWS Budgets notifications. Defaults to alert_email."
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "GitHub repo allowed to assume the deploy role, as owner/name."
  type        = string
  default     = ""
}

variable "enable_databricks" {
  description = "Create Databricks secrets, monitor, schedule, and model alarms."
  type        = bool
  default     = true
}

variable "enable_github_deploy" {
  description = "Create the GitHub OIDC provider and deployment role."
  type        = bool
  default     = true
}

variable "create_github_oidc_provider" {
  description = "Set false if the account already has the GitHub OIDC provider."
  type        = bool
  default     = true
}

variable "f1_alarm_threshold" {
  description = "Alarm when LiveMacroF1 drops below this."
  type        = number
  default     = 0.45
}

variable "psi_alarm_threshold" {
  description = "Alarm when MaxPSI exceeds this (0.2 = 'significant shift' rule of thumb)."
  type        = number
  default     = 0.2
}

variable "log_retention_days" {
  type    = number
  default = 14
}
