#!/usr/bin/env bash
# Lint + unit tests + backtest (no AWS, no API key needed). Optional live evals.
#   jenkins/scripts/run_checks.sh                 # what CI runs on every commit
#   RUN_LIVE_EVALS=true jenkins/scripts/run_checks.sh   # also call Claude (needs ANTHROPIC_API_KEY, ~$0.02)
set -euo pipefail
cd "$(dirname "$0")/../.."

PY=${PYTHON:-python3}
if [[ ! -x .venv/bin/python ]]; then
  echo "==> creating .venv"
  $PY -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python - <<'PYV'
import sys
if sys.version_info < (3, 12):
    sys.exit(f"Python 3.12+ required, found {sys.version.split()[0]}. Set PYTHON=python3.13")
PYV
echo "==> installing requirements"
pip install --quiet --disable-pip-version-check -r requirements.txt

mkdir -p reports
echo "==> ruff (lint)"
ruff check app ui tests evals

echo "==> pytest (unit + guardrails + prompt injection + API)"
pytest --junitxml=reports/pytest.xml

echo "==> backtest (generated shop; run 'python -m tests.backtest' locally for the real data)"
python -m tests.backtest --demo

if [[ "${RUN_LIVE_EVALS:-false}" == "true" ]]; then
  : "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY for live evals}"
  echo "==> live evals against Claude (demo shop)"
  export DATA_DIR="$(mktemp -d)" CSV_DIR=/nonexistent
  export DATABASE_URL="sqlite:///$DATA_DIR/shop.db" MEMORY_URL="sqlite:///$DATA_DIR/memory.db"
  python -m app.ingest --demo
  python -m evals.run --local --only greeting,unsafe_delete,injection_direct,injection_audit,pii_emails,users_brasil
  cp evals/results.json reports/eval-results.json
fi
echo "==> all checks passed"
