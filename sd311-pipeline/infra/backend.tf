# Remote state lives in an S3 bucket you create ONCE by hand (see README,
# "Deploy"). The settings are passed at init time so nothing account-specific
# is committed:
#
#   terraform init \
#     -backend-config="bucket=<your-tf-state-bucket>" \
#     -backend-config="key=sd311-main/terraform.tfstate" \
#     -backend-config="region=us-east-2"
#
# CI's validate step uses `terraform init -backend=false`, which skips this.
terraform {
  backend "s3" {}
}
