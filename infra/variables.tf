variable "project" {
  description = "Prefix for every resource name"
  type        = string
  default     = "insightly"
}

variable "environment" {
  description = "dev | staging | prod"
  type        = string
  default     = "prod"
}

variable "region" {
  description = "AWS region (ap-south-1 = Mumbai)"
  type        = string
  default     = "ap-south-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "kubernetes_version" {
  description = "EKS version. List supported ones: aws eks describe-cluster-versions --query 'clusterVersions[].clusterVersion'"
  type        = string
  default     = "1.35"
}

variable "node_instance_types" {
  description = "EKS worker node types (2 vCPU / 8 GiB is plenty for this app + monitoring)"
  type        = list(string)
  default     = ["t3.large"]
}

variable "node_min" {
  type    = number
  default = 2
}

variable "node_max" {
  type    = number
  default = 4
}

variable "node_desired" {
  type    = number
  default = 2
}

variable "cluster_admin_arns" {
  description = "Extra IAM user/role ARNs that get cluster-admin on EKS (the identity running terraform always does)"
  type        = list(string)
  default     = []
}

variable "eks_public_access_cidrs" {
  description = "Who can reach the EKS API endpoint (kubectl). Use your IP/32 for production."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 30
}

variable "postgres_version" {
  description = "RDS PostgreSQL major version"
  type        = string
  default     = "17"
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "db_deletion_protection" {
  description = "Set true for production; false lets `terraform destroy` remove the database"
  type        = bool
  default     = false
}

variable "alert_email" {
  description = "Email for CloudWatch alarms (confirm the SNS subscription email). Empty = no email."
  type        = string
  default     = ""
}

variable "daily_cost_alarm_usd" {
  description = "Alarm when LLM spend reported by the app exceeds this per day"
  type        = number
  default     = 5
}

variable "log_retention_days" {
  type    = number
  default = 30
}
