# Insightly on AWS: VPC, EKS, ECR, RDS PostgreSQL, S3, Secrets Manager, CloudWatch.
#
# From the VS Code terminal (uses your AWS CLI credentials / AWS_PROFILE):
#   cd infra
#   terraform init
#   terraform plan  -out tf.plan
#   terraform apply tf.plan
#
# Remote state (recommended for teams): create a bucket once, then uncomment the backend
# block and run `terraform init -migrate-state`. See docs/aws-deployment.md.
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }

  # backend "s3" {
  #   bucket       = "insightly-tfstate-<account-id>"
  #   key          = "insightly/terraform.tfstate"
  #   region       = "ap-south-1"
  #   use_lockfile = true      # S3-native state locking (no DynamoDB table needed)
  #   encrypt      = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "insightly"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

locals {
  name       = "${var.project}-${var.environment}"     # e.g. insightly-prod
  account_id = data.aws_caller_identity.current.account_id
  azs        = slice(data.aws_availability_zones.available.names, 0, 3)
  namespace  = "insightly"                              # Kubernetes namespace (k8s/namespace.yaml)
}
