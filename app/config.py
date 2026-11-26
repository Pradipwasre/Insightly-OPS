"""All settings in one place.

Values come from environment variables. When you run from the VS Code terminal,
the `.env` file in the project root is loaded automatically (see .env.example).
On EKS the same variables come from the ConfigMap and from AWS Secrets Manager.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# .env never overrides variables that are already set (Docker / Kubernetes win)
load_dotenv(ROOT / ".env", override=False)


def _bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _path(value):
    """Relative paths are relative to the project root, so commands work from any folder."""
    p = Path(value).expanduser()
    return str(p if p.is_absolute() else ROOT / p)


ENV = os.getenv("APP_ENV", "local")  # local | dev | prod

# ---------- LLM ----------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

PROVIDER = (os.getenv("LLM_PROVIDER", "") or (
    "anthropic" if ANTHROPIC_API_KEY else "openai" if OPENAI_API_KEY
    else "groq" if GROQ_API_KEY else "anthropic")).lower()

DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openai": "gpt-5.4-mini", "groq": "openai/gpt-oss-120b"}
MODEL = os.getenv("LLM_MODEL", "") or DEFAULT_MODELS.get(PROVIDER, "claude-haiku-5-5")
# Small background jobs (routing, memory summary) always use the cheapest model
FAST_MODELS = {"anthropic": "claude-haiku-5-5"}
FAST_MODEL = os.getenv("LLM_FAST_MODEL", "") or FAST_MODELS.get(PROVIDER, MODEL)
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "120"))

# ---------- data ----------
CSV_DIR = _path(os.getenv("CSV_DIR", "data/raw"))   # CSV files (file name = table name)
DATA_DIR = _path(os.getenv("DATA_DIR", "data"))     # files the app creates
os.makedirs(DATA_DIR, exist_ok=True)

# Business data. SQLite locally; on AWS: postgresql+psycopg://user:pass@rds-host:5432/insightly
def _db_url(value, default_file):
    """sqlite:///relative/path.db is resolved from the project root, like CSV_DIR."""
    url = value or f"sqlite:///{os.path.join(DATA_DIR, default_file)}"
    if url.startswith("sqlite:///"):
        url = "sqlite:///" + _path(url[len("sqlite:///"):])
    return url


DATABASE_URL = _db_url(os.getenv("DATABASE_URL", ""), "shop.db")
# Chat memory (LangGraph checkpoints). Defaults to SQLite; on EKS use the RDS database
# so every API pod shares the same conversations.
MEMORY_URL = _db_url(os.getenv("MEMORY_URL", ""), "memory.db")
# Build the database from CSV_DIR on API start if it is missing (local only; on AWS the ingest Job does it)
AUTO_INGEST = _bool("AUTO_INGEST", ENV == "local")
QUERY_TIMEOUT_S = int(os.getenv("QUERY_TIMEOUT_S", "60"))
MAX_ROWS = int(os.getenv("MAX_ROWS", "50"))

# ---------- AWS ----------
AWS_REGION = os.getenv("AWS_REGION", "") or os.getenv("AWS_DEFAULT_REGION", "") or "ap-south-1"
S3_BUCKET = os.getenv("S3_BUCKET", "")              # raw CSVs live here on AWS
S3_PREFIX = os.getenv("S3_PREFIX", "raw/").strip("/") + "/"
CLOUDWATCH_ENABLED = _bool("CLOUDWATCH_ENABLED", False)
CLOUDWATCH_NAMESPACE = os.getenv("CLOUDWATCH_NAMESPACE", "Insightly")
CLOUDWATCH_LOG_GROUP = os.getenv("CLOUDWATCH_LOG_GROUP", "/insightly/api")

# ---------- guardrails ----------
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "2000"))
INJECTION_BLOCK_SCORE = float(os.getenv("INJECTION_BLOCK_SCORE", "0.6"))
# Columns the agent may never return row by row (personal data)
PII_COLUMNS = {c.strip().lower() for c in os.getenv(
    "PII_COLUMNS", "email,street_address,ip_address,latitude,longitude,user_latitude,user_longitude").split(",") if c.strip()}
RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "25"))

# ---------- prices (USD per 1M tokens, standard tier, checked Nov 2026) ----------
PRICES = {"claude-haiku-5-5": {"input": 0.10, "output": 0.50},   # prompts up to 100K tokens (ours ~3K)
          "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
          "claude-opus-5-5": {"input": 4.00, "output": 20.00},
          "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
          "gpt-5.4": {"input": 2.50, "output": 15.00}}


def cost_usd(tokens, model=None):
    p = PRICES.get(model or MODEL, PRICES["claude-haiku-5-5"])
    return (tokens["input"] * p["input"] + tokens["output"] * p["output"]) / 1_000_000


def redacted_url(url):
    """postgresql://user:secret@host/db -> postgresql://user:***@host/db (safe to log)."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        return f"{scheme}://{creds.split(':')[0]}:***@{host}"
    return url
