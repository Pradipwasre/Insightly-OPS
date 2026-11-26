# 3 AZs: public subnets (load balancer, NAT), private subnets (EKS nodes), database subnets (RDS).
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.0"

  name = local.name
  cidr = var.vpc_cidr
  azs  = local.azs

  public_subnets   = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, i)]
  private_subnets  = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, i + 1)]
  database_subnets = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, i + 100)]

  create_database_subnet_group = true
  enable_nat_gateway           = true
  single_nat_gateway           = var.environment != "prod" # one NAT per AZ in prod, one shared otherwise
  enable_dns_hostnames         = true

  # Tags the AWS cloud controller uses to place load balancers
  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
}

# S3 traffic from the private subnets stays on the AWS network (and skips NAT charges)
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids
  tags              = { Name = "${local.name}-s3" }
}
