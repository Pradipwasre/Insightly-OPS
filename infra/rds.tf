# PostgreSQL on RDS: business data (loaded by the ingest Job) + chat memory (LangGraph checkpoints).
# Only the EKS nodes can reach it; it has no public address.
resource "random_password" "db_admin" {
  length           = 32
  special          = true
  override_special = "-_" # URL-safe, so it can go straight into a connection string
}

resource "random_password" "db_readonly" {
  length           = 32
  special          = true
  override_special = "-_"
}

resource "aws_security_group" "rds" {
  name        = "${local.name}-rds"
  description = "PostgreSQL from EKS nodes only"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_eks" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = module.eks.node_security_group_id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "EKS nodes"
}

resource "aws_db_parameter_group" "pg" {
  name   = "${local.name}-pg${var.postgres_version}"
  family = "postgres${var.postgres_version}"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "5000" # log queries slower than 5 s
  }
}

resource "aws_db_instance" "main" {
  identifier     = local.name
  engine         = "postgres"
  engine_version = var.postgres_version
  instance_class = var.db_instance_class

  db_name  = "insightly"
  username = "insightly_admin"
  password = random_password.db_admin.result

  allocated_storage     = var.db_allocated_storage_gb
  max_allocated_storage = var.db_storage_autoscaling ? var.db_allocated_storage_gb * 4 : null
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = module.vpc.database_subnet_group_name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  multi_az               = var.db_multi_az
  parameter_group_name   = aws_db_parameter_group.pg.name

  backup_retention_period         = var.db_backup_retention_days
  auto_minor_version_upgrade      = true
  performance_insights_enabled    = !strcontains(var.db_instance_class, "micro") # not on Free-Tier micro classes
  enabled_cloudwatch_logs_exports = ["postgresql"]
  deletion_protection             = var.db_deletion_protection
  skip_final_snapshot             = !var.db_deletion_protection
  final_snapshot_identifier       = var.db_deletion_protection ? "${local.name}-final" : null
}
