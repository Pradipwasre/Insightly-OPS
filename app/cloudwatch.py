"""Observability: structured logs, Prometheus metrics and (optionally) Amazon CloudWatch.

* Logs are one JSON object per line on stdout. On EKS, Container Insights /
  Fluent Bit ship them to the CloudWatch log group automatically.
* Prometheus metrics are served at /metrics (scraped by the ServiceMonitor, shown in Grafana).
* With CLOUDWATCH_ENABLED=true the same numbers are also sent to CloudWatch
  custom metrics (namespace Insightly) in the background, once a minute, so
  CloudWatch alarms work even without Prometheus. Failures never break a request.
"""
import json
import logging
import sys
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime

from prometheus_client import Counter, Histogram

from app import config

# ---------- logs ----------


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "level": record.levelname,
               "logger": record.name, "msg": record.getMessage(), "env": config.ENV}
        out.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging():
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpx2", "httpcore", "botocore", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


log = logging.getLogger("insightly")


def event(msg, **fields):
    log.info(msg, extra={"extra_fields": fields})


# ---------- Prometheus ----------
REQUESTS = Counter("insightly_requests_total", "Chat requests", ["route", "status"])
LATENCY = Histogram("insightly_request_latency_seconds", "End-to-end chat latency", ["route"],
                    buckets=(0.5, 1, 2, 4, 8, 15, 30, 60, 120))
TOKENS = Counter("insightly_llm_tokens_total", "LLM tokens", ["kind"])
COST = Counter("insightly_llm_cost_usd_total", "LLM cost in USD")
GUARDRAIL = Counter("insightly_guardrail_blocks_total", "Requests or queries blocked by a guardrail", ["category"])
SQL_ERRORS = Counter("insightly_sql_errors_total", "SQL errors returned to the agent")
TOOL_CALLS = Counter("insightly_tool_calls_total", "Tool calls made by the agents", ["tool"])


class Metrics:
    """One place to record what happened. Prometheus always; CloudWatch when enabled."""

    def __init__(self):
        self._buffer = defaultdict(list)   # metric name -> values
        self._lock = threading.Lock()
        self._thread = None

    # --- recorders ---
    def request(self, route, status, latency_s, tokens, cost, tools=()):
        REQUESTS.labels(route, status).inc()
        LATENCY.labels(route).observe(latency_s)
        TOKENS.labels("input").inc(tokens.get("input", 0))
        TOKENS.labels("output").inc(tokens.get("output", 0))
        COST.inc(cost)
        for t in tools:
            TOOL_CALLS.labels(t).inc()
        self._put("Requests", 1)
        self._put("Errors", 1 if status == "error" else 0)
        self._put("LatencySeconds", latency_s, "Seconds")
        self._put("Tokens", tokens.get("input", 0) + tokens.get("output", 0))
        self._put("CostUSD", cost, "None")

    def guardrail_block(self, category):
        GUARDRAIL.labels(category).inc()
        self._put("GuardrailBlocks", 1)

    def sql_error(self):
        SQL_ERRORS.inc()
        self._put("SqlErrors", 1)

    # --- CloudWatch ---
    def _put(self, name, value, unit="Count"):
        if not config.CLOUDWATCH_ENABLED:
            return
        with self._lock:
            self._buffer[(name, unit)].append(float(value))
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="cloudwatch-flush")
            self._thread.start()

    def _loop(self):
        while True:
            time.sleep(60)
            self.flush()

    def flush(self):
        with self._lock:
            buffered, self._buffer = self._buffer, defaultdict(list)
        if not buffered:
            return 0
        data = [{"MetricName": name, "Unit": unit,
                 "Dimensions": [{"Name": "Environment", "Value": config.ENV}],
                 "StatisticValues": {"SampleCount": len(v), "Sum": sum(v), "Minimum": min(v), "Maximum": max(v)}}
                for (name, unit), v in buffered.items()]
        try:
            import boto3
            cw = boto3.client("cloudwatch", region_name=config.AWS_REGION)
            for i in range(0, len(data), 20):
                cw.put_metric_data(Namespace=config.CLOUDWATCH_NAMESPACE, MetricData=data[i:i + 20])
        except Exception as e:  # monitoring must never take the app down
            log.warning("cloudwatch flush failed: %s", e)
            return 0
        return len(data)


metrics = Metrics()
