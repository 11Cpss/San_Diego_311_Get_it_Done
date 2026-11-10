output "bucket_name" {
  value = aws_s3_bucket.data.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.lambda.repository_url
}

output "ingest_function_name" {
  value = aws_lambda_function.ingest.function_name
}

output "monitor_function_name" {
  value = try(aws_lambda_function.monitor[0].function_name, null)
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "github_deploy_role_arn" {
  description = "Put this in the GitHub 'prod' environment as AWS_DEPLOY_ROLE_ARN."
  value       = try(aws_iam_role.github_deploy[0].arn, null)
}
