# Insightly AWS: AI data analyst on Amazon EKS

Ask questions about an online shop's data in plain English. A team of LangGraph agents
(Claude via your Anthropic API key) writes read-only SQL, draws Plotly charts and answers
with real numbers. Guardrails stop prompt injection, data changes and personal-data leaks.
It runs on your laptop from the VS Code terminal, and on AWS (EKS, RDS, S3, ECR,
Secrets Manager, CloudWatch) with Terraform, Docker, Kubernetes and Jenkins.

```
Browser ─▶ Streamlit UI ─▶ FastAPI ─▶ LangGraph:  guard_in ─▶ router ─▶ analyst / chart / chat ─▶ guard_out ─▶ memory
                                          │                                │  tools: get_schema · lookup_metric · run_sql · make_chart
                                          ▼                                ▼
                               Prometheus /metrics           SQLite (laptop) │ RDS PostgreSQL (AWS, read-only user)
                               CloudWatch metrics + logs     raw CSVs: local folder │ S3
```

## Quick start (VS Code terminal, macOS)

```bash
cd "Insightly Ops "
make setup          # .venv with Python 3.12+ and the pinned libraries, creates .env
#  -> put ANTHROPIC_API_KEY in .env (already done if Claude set this folder up)
make backtest       # checks everything against your real data (no API key, ~10 s)
make api            # terminal 1: http://localhost:8000/docs
make ui             # terminal 2: http://localhost:8501
```

No `make`? Every target is a plain command, e.g. `python -m tests.backtest`, `uvicorn app.main:app --reload`,
`streamlit run ui/app.py`. VS Code also has them under **Terminal > Run Task** and **Run and Debug**.

`.env` points at your data:

```bash
CSV_DIR=data files                    # the 7 CSV files
DATABASE_URL=sqlite:///data/shop.db     # built from them (make ingest-force rebuilds it)
```

## Everyday commands

| Command | What it does |
|---|---|
| `make test` | 86 unit tests: guardrails, prompt injection, API, agent graph (scripted LLM, free) |
| `make backtest` | 45 checks on the real data: row counts, cleaning, SQL safety, guardrails, charts, agent graph |
| `make eval` | 25 live questions answered by Claude and scored against SQL ground truth (~$0.01) |
| `make check` | everything CI runs (lint + tests + backtest) |
| `make ingest-force` | rebuild the database from the CSVs (~25 s) |
| `make aws-check` | `aws sts get-caller-identity`: which AWS account the CLI is using |
| `make infra-plan` / `make infra-apply` | Terraform: VPC, EKS, RDS, S3, ECR, Secrets Manager, CloudWatch |
| `make secret-anthropic` | copy the key from `.env` into AWS Secrets Manager (never printed) |
| `make s3-upload` | upload the CSVs to S3 |
| `make push` | build linux/amd64 images and push them to ECR |
| `make deploy-bootstrap` / `make deploy` | first deploy (Helm add-ons + ingest) / update |
| `make status` · `make logs` · `make ui-url` · `make grafana` · `make cloudwatch-logs` | operate the cluster |

The full AWS walkthrough is in [docs/aws-deployment.md](docs/aws-deployment.md).

## Verified results (25 Nov 2026)

| Check | Result |
|---|---|
| pytest (SQLite and PostgreSQL) | 86 / 86 passed |
| Backtest on the real data (SQLite and PostgreSQL read-only user) | 45 / 45 passed |
| Live evals, claude-haiku-5-5, real data | 25 / 25 correct · 100% tool-call accuracy · 0 prompt leaks · $0.0106 total · 2.8 s mean |
| Prompt-injection corpus | 20 / 20 attacks blocked · 0 / 10 false positives |

Details: [docs/REPORT.md](docs/REPORT.md) and `evals/results.json`.

## Project layout

```
app/        config · llm · db (SQLite/Postgres) · storage (S3) · ingest · knowledge · charts
            tools · graph (agents) · guardrails · cloudwatch (logs/metrics) · main (FastAPI)
ui/         Streamlit web app
tests/      conftest + fakes (scripted LLM) · backtest · guardrails · prompt injection · API
evals/      run.py · test_set.json · metrics/ (core, hallucination, guardrails, prompt_injection, agent_failures)
docker/     Dockerfile.api · Dockerfile.ui · Dockerfile.ingest
k8s/        namespace · configmap · external-secret · api/ui deployments+services · hpa · ingest job · servicemonitor
infra/      Terraform: vpc · eks · ecr · rds · s3 · secrets · monitoring
monitoring/ Prometheus config · Grafana dashboard · alert rules
jenkins/    run_checks.sh · build_and_push.sh · deploy_eks.sh · render_k8s.py   (+ Jenkinsfile)
docs/       architecture.html · agentic-ai.html · REPORT.md · aws-deployment.md
```

## Library versions

All pins in `requirements.txt` were checked against PyPI on 25 Nov 2026 and installed together on
Python 3.13 (3.12 also resolves). The old project pinned versions that don't exist
(`fastapi==0.162.7`, `langchain-core==1.8.0`, `langchain-openai==1.8.0`), so `pip install` failed.
Key versions: fastapi 0.165.0 · langchain-core 1.8.4 · langchain-anthropic 1.7.12 · langgraph 1.2.24 ·
pandas 3.1.1 · plotly 7.2.0 · streamlit 1.67.0 · SQLAlchemy 2.1.9 · psycopg 3.3.9 · boto3 1.44.11.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `401 Unauthorized` from Anthropic | Test the key: `curl https://api.anthropic.com/v1/models -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01"`. A corporate proxy/VPN that filters `api.anthropic.com` causes the same error. |
| `Python 3.12+ required` | `brew install python@3.13`, then `make setup` |
| `No database at ...` | `make ingest` (or check `DATABASE_URL` / `CSV_DIR` in `.env`) |
| API `/ready` returns 503 | the message says why: empty database, or missing API key |
| Images crash on EKS with `exec format error` | build with `make push` (forces `linux/amd64`), not a plain `docker build` on an M-series Mac |
