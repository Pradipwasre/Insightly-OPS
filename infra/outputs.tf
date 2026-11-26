# `terraform output` values used by the Makefile and jenkins/scripts/*.sh
output "region" {
  value = var.region
}

output "environment" {
  value = var.environment
}

output "name" {
  value = local.name
}

output "account_id" {
  value = local.account_id
}

output "cluster_name" {
  value = module.eks.cluster_name
}

output "ecr_registry" {
  value = "${local.account_id}.dkr.ecr.${var.region}.amazonaws.com"
}

output "ecr_repositories" {
  value = { for k, r in aws_ecr_repository.app : k => r.repository_url }
}

output "s3_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "rds_endpoint" {
  value = aws_db_instance.main.address
}

output "anthropic_secret_name" {
  value = aws_secretsmanager_secret.anthropic.name
}

output "db_secret_name" {
  value = aws_secretsmanager_secret.db.name
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "configure_kubectl" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}
