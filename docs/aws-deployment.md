# Deploying Insightly to AWS from the VS Code terminal

Everything below runs in the VS Code integrated terminal on your Mac, using the AWS CLI
with your own credentials. Each step lists the `make` target and the raw command behind it.

**Rough monthly cost** (ap-south-1, always on, order of magnitude only; check the
[AWS Pricing Calculator](https://calculator.aws/)): EKS control plane + 2 × t3.large nodes +
NAT gateway(s) + db.t4g.medium RDS + a Network Load Balancer comes to a few hundred US dollars
a month. Claude Haiku 5.5 usage is about $0.0004 per question. `make infra-destroy` removes everything.

---

## 0. Tools (once)

```bash
brew install awscli terraform kubectl helm python@3.13
brew install --cask docker          # Docker Desktop (includes buildx)
aws --version && terraform -version && kubectl version --client && helm version
```

## 1. AWS credentials

Use the credentials you already have. Pick **one** way:

```bash
aws configure                        # access key + secret, region ap-south-1
# or, for IAM Identity Center (SSO):
aws configure sso && aws sso login --profile <profile>
```

If you use a named profile, put it in `.env` so `make`, Python (boto3) and Terraform all use it:

```bash
AWS_PROFILE=<profile>
AWS_REGION=ap-south-1
```

Check it:

```bash
make aws-check                       # = aws sts get-caller-identity
```

The identity needs permissions to create VPC, EKS, IAM roles, RDS, S3, ECR, Secrets Manager,
CloudWatch and SNS resources (AdministratorAccess in a sandbox account is simplest).

## 2. (Recommended) Remote Terraform state

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws s3api create-bucket --bucket insightly-tfstate-$ACCOUNT --region ap-south-1 \
  --create-bucket-configuration LocationConstraint=ap-south-1
aws s3api put-bucket-versioning --bucket insightly-tfstate-$ACCOUNT --versioning-configuration Status=Enabled
```

Then uncomment the `backend "s3"` block in `infra/main.tf` (put your account id in the bucket name).
Jenkins needs remote state to read `terraform output`.

## 3. Create the infrastructure (~20 minutes)

```bash
make infra-init                      # terraform -chdir=infra init
make infra-plan                      # creates infra/terraform.tfvars from the example, then plans
make infra-apply                     # terraform -chdir=infra apply tf.plan
make infra-output                    # cluster name, ECR registry, S3 bucket, RDS endpoint...
```

Review `infra/terraform.tfvars` first. Things you may want to change:
`kubernetes_version` (list valid ones with `aws eks describe-cluster-versions --query 'clusterVersions[].clusterVersion'`),
`alert_email`, `eks_public_access_cidrs` (your IP/32), `db_deletion_protection = true` for production.

What gets created:

| File | Resources |
|---|---|
| `vpc.tf` | VPC over 3 AZs: public / private / database subnets, NAT, S3 gateway endpoint |
| `eks.tf` | EKS cluster + managed node group, Pod Identity, CloudWatch observability add-on, IAM roles for the API and ingest pods |
| `ecr.tf` | `insightly-api`, `insightly-ui`, `insightly-ingest` repositories (immutable tags, scan on push) |
| `rds.tf` | PostgreSQL 17, encrypted, private, TLS enforced, backups, Performance Insights |
| `s3.tf` | private, versioned, encrypted, TLS-only bucket for raw CSVs |
| `secrets.tf` | `<name>/anthropic` (empty) and `<name>/db` (connection strings) + IAM for External Secrets |
| `monitoring.tf` | log groups, alarms (errors, latency, guardrail spikes, LLM cost, RDS), SNS topic, dashboard |

## 4. Put the Anthropic key in Secrets Manager

```bash
make secret-anthropic
```

This reads `ANTHROPIC_API_KEY` from `.env`, writes it to a private temp file and runs
`aws secretsmanager put-secret-value --secret-id insightly-prod/anthropic --secret-string file://...`.
The key never appears on screen, in shell history or in Terraform state.

## 5. Upload the data to S3

```bash
make s3-upload                       # = python -m app.ingest --upload   (about 530 MB)
aws s3 ls s3://$(terraform -chdir=infra output -raw s3_bucket)/raw/ --human-readable
```

## 6. Build and push the images

```bash
git add -A && git commit -m "insightly aws"     # the image tag is the git commit
make push                            # = jenkins/scripts/build_and_push.sh
```

It logs Docker in to ECR (`aws ecr get-login-password | docker login ...`), builds all three images for
`linux/amd64` (EKS nodes are x86; Apple Silicon would otherwise build arm64), pushes them and
prints the ECR vulnerability-scan counts.

## 7. Deploy to EKS

```bash
make deploy-bootstrap                # first time only
```

That script:

1. `aws eks update-kubeconfig` so `kubectl` talks to the new cluster
2. installs with Helm: External Secrets Operator, metrics-server (for autoscaling), kube-prometheus-stack (Prometheus, Grafana, Alertmanager)
3. applies `namespace`, `configmap`, `external-secret` and waits until the Kubernetes Secret is synced from Secrets Manager
4. runs the **ingest Job**: downloads the CSVs from S3 and loads RDS (~1 minute), creating the read-only DB user
5. applies the API and UI deployments, services and autoscalers and waits for the rollouts
6. adds the ServiceMonitor and alert rules, then smoke-tests `/ready` from inside the API pod

Later updates are just `make push && make deploy`. Reload data after new CSVs: `make s3-upload && make ingest-eks`.

## 8. Use it and watch it

```bash
make ui-url                          # http://<nlb-hostname>   (DNS can take 2-3 minutes)
make status                          # pods, services, HPA, external secret, jobs
make logs                            # API logs (JSON)
make grafana                         # http://localhost:3000, dashboard "Insightly - Overview"
make cloudwatch-logs                 # the same logs in CloudWatch
aws cloudwatch describe-alarms --alarm-name-prefix insightly --query 'MetricAlarms[].[AlarmName,StateValue]' --output table
```

Restrict who can open the UI: `UI_ALLOWED_CIDR=<your-ip>/32 make deploy`.

## 9. Jenkins (optional)

`Jenkinsfile` runs `run_checks.sh` on every branch. On `main` it also runs a live eval subset, pushes
images, asks for approval and deploys. Create two Jenkins credentials:
`anthropic-api-key` (Secret text) and `aws-insightly` (AWS credentials), or run the agent on EC2 with an
IAM role. The agent needs python3.12+, docker+buildx, aws, kubectl, helm and terraform, plus remote state (step 2).

## 10. Tear down

```bash
kubectl delete namespace insightly   # removes the load balancer first
make infra-destroy                   # terraform destroy (type 'yes')
```

## Troubleshooting

| Problem | Check |
|---|---|
| `ExternalSecret` not Ready | `kubectl -n insightly describe externalsecret insightly-secrets`. Usually the Anthropic secret has no value yet: `make secret-anthropic` |
| API pods not Ready | `kubectl -n insightly logs deploy/insightly-api`. `/ready` says "database is empty" until the ingest job has run |
| Ingest job failed | `kubectl -n insightly logs job/insightly-ingest-<tag>` |
| `exec format error` | the image was built for arm64; use `make push` |
| UI URL empty | `kubectl -n insightly get svc insightly-ui -w`; check the public subnets have the `kubernetes.io/role/elb` tag (vpc.tf sets it) |
| Terraform: unsupported Kubernetes version | set `kubernetes_version` to one from `aws eks describe-cluster-versions` |
