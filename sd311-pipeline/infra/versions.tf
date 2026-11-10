terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80" # any 5.x >= 5.80; bump deliberately, never float to 6.x
    }
  }
}

provider "aws" {
  region = var.region

  # Every resource gets these tags -> easy to find in the console / Cost Explorer.
  default_tags {
    tags = {
      Project   = "sd311"
      ManagedBy = "terraform"
    }
  }
}
