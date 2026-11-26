"""Tools the agents can call. The docstring is what the LLM reads to decide.

Every query passes three guardrails: check_sql (read-only SELECT, no personal
data), the read-only connection with a timeout (app/db.py), and redact_rows.
Results are wrapped so instructions hidden in data are flagged, never followed.
"""
from typing import Literal

from langchain_core.tools import tool

from app import charts, db, guardrails, knowledge
from app.cloudwatch import metrics


def _guarded_query(sql, max_rows=None):
    verdict = guardrails.check_sql(sql)
    if not verdict.allowed:
        metrics.guardrail_block(verdict.category)
        raise PermissionError(verdict.reason)
    cols, rows = db.query(sql, max_rows=max_rows)
    return guardrails.redact_rows(cols, rows)


@tool
def get_schema() -> str:
    """List every table and its columns in the shop database, plus the SQL dialect."""
    return db.schema()


@tool
def lookup_metric(question: str) -> str:
    """Find the official definition of a business metric (revenue, profit, return rate...)."""
    hits = knowledge.search(question)
    if not hits:
        return "No official metric found. Use your best judgement."
    return "\n".join(f"{m['name']}: {m['sql']}" for m in hits)


@tool
def run_sql(sql: str) -> str:
    """Run one read-only SELECT query and return the rows (max 50)."""
    try:
        cols, rows = _guarded_query(sql)
    except PermissionError as e:
        return str(e)
    except Exception as e:  # the error goes back to the LLM so it can fix its SQL
        metrics.sql_error()
        return f"SQL error: {str(e).splitlines()[0][:300]}"
    return guardrails.wrap_tool_output(f"columns: {cols}\nrows: {rows}")


@tool(response_format="content_and_artifact")
def make_chart(sql: str, chart_type: Literal["bar", "line", "area", "pie", "scatter"],
               x: str, y: str, title: str, color: str = ""):
    """Draw a Plotly chart from ONE read-only SELECT query.
    x, y (and optional color, for a second grouping) must be column names in the query result.
    bar = compare categories, line = trend over time, area = volume over time,
    pie = share of a total (8 slices or fewer), scatter = relation between two numbers.
    Aggregate in SQL first (e.g. by month or category) and keep it to about 30 rows."""
    try:
        cols, rows = _guarded_query(sql, max_rows=500)
    except PermissionError as e:
        return str(e), None
    except Exception as e:
        metrics.sql_error()
        return f"Chart error: {str(e).splitlines()[0][:300]}", None
    try:
        figure, summary = charts.build(cols, rows, chart_type, x, y, title, color)
    except Exception as e:  # back to the LLM so it can fix the query or the column names
        return f"Chart error: {e}", None
    return guardrails.wrap_tool_output(summary), figure


TOOLS = [get_schema, lookup_metric, run_sql]   # the analyst agent
CHART_TOOLS = TOOLS + [make_chart]             # the chart agent
