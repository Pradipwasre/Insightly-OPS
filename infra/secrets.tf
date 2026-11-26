# Two secrets in AWS Secrets Manager:
#   <name>/anthropic  your Anthropic API key. Terraform creates it EMPTY so the key never
#                     enters Terraform state; set it once from the VS Code terminal:
#                       make secret-anthropic        (reads ANTHROPIC_API_KEY from .env)
#   <name>/db         database URLs + read-only password, written by Terraform.
resource "aws_secretsmanager_secret" "anthropic" {
  name                    = "${local.name}/anthropic"
  description             = "Anthropic API key for the Insightly agents"
  recovery_window_in_days = var.environment == "prod" ? 30 : 0
}

resource "aws_secretsmanager_secret" "db" {
  name                    = "${local.name}/db"
  description             = "Insightly PostgreSQL connection strings"
  recovery_window_in_days = var.environment == "prod" ? 30 : 0
}

locals {
  db_host = aws_db_instance.main.address
  db_name = aws_db_instance.main.db_name
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    admin_url         = "postgresql://insightly_admin:${random_password.db_admin.result}@${local.db_host}:5432/${local.db_name}?sslmode=require"
    readonly_url      = "postgresql://insightly_ro:${random_password.db_readonly.result}@${local.db_host}:5432/${local.db_name}?sslmode=require"
    readonly_password = random_password.db_readonly.result
    host              = local.db_host
  })
}

# External Secrets Operator (namespace external-secrets) may read only these two secrets
resource "aws_iam_role" "external_secrets" {
  name               = "${local.name}-external-secrets"
  assume_role_policy = data.aws_iam_policy_document.pod_trust.json
}

resource "aws_iam_role_policy" "external_secrets" {
  role = aws_iam_role.external_secrets.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      Resource = [aws_secretsmanager_secret.anthropic.arn, aws_secretsmanager_secret.db.arn]
    }]
  })
}

resource "aws_eks_pod_identity_association" "external_secrets" {
  cluster_name    = module.eks.cluster_name
  namespace       = "external-secrets"
  service_account = "external-secrets"
  role_arn        = aws_iam_role.external_secrets.arn
}
