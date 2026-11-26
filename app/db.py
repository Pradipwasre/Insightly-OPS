"""The shop database: SQLite on your laptop, PostgreSQL (Amazon RDS) on AWS.

Everything the agent runs goes through query(), which opens a READ-ONLY
connection with a timeout, so a bad or malicious query can never change data
or hang the API.
"""
import sqlite3
import time
from decimal import Decimal
from functools import lru_cache

from app import config

SQLITE_HINT = "Dialect: SQLite. Dates are text like '2023-09-23 08:12:00'; use strftime('%Y', created_at) for the year."
POSTGRES_HINT = ("Dialect: PostgreSQL. Dates are text like '2023-09-23 08:12:00'; strftime('%Y', created_at) "
                 "works here too (helper function). Use CASE WHEN ... THEN 1 ELSE 0 END inside SUM, not booleans.")


def dialect(url=None):
    url = url or config.DATABASE_URL
    return "postgresql" if url.startswith(("postgres", "postgresql")) else "sqlite"


def sqlite_path(url=None):
    url = url or config.DATABASE_URL
    return url.split("sqlite:///", 1)[1] if url.startswith("sqlite:///") else url


def pg_url(url=None):
    """Any postgres URL -> the SQLAlchemy + psycopg 3 form."""
    url = url or config.DATABASE_URL
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def pg_dsn(url=None):
    """Plain libpq DSN for psycopg / LangGraph (no '+psycopg')."""
    return pg_url(url).replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache(maxsize=1)
def engine():
    from sqlalchemy import create_engine
    return create_engine(pg_url(), pool_size=5, max_overflow=5, pool_pre_ping=True, pool_recycle=1800)


# ---------- read-only queries (what the agent uses) ----------
def _sqlite_read_only():
    con = sqlite3.connect(f"file:{sqlite_path()}?mode=ro", uri=True, check_same_thread=False)
    deadline = time.time() + config.QUERY_TIMEOUT_S
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10_000)  # stops runaway queries
    return con


def query(sql, max_rows=None):
    """Run one SQL statement read-only. Returns (column names, rows)."""
    max_rows = max_rows or config.MAX_ROWS
    if dialect() == "sqlite":
        con = _sqlite_read_only()
        try:
            cur = con.execute(sql)
            return [c[0] for c in cur.description or []], cur.fetchmany(max_rows)
        finally:
            con.close()
    raw = engine().raw_connection()   # plain psycopg connection: no SQLAlchemy parsing of '%' in SQL
    try:
        with raw.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(f"SET LOCAL statement_timeout = '{int(config.QUERY_TIMEOUT_S)}s'")
            cur.execute(sql)
            cols = [c[0] for c in cur.description or []]
            rows = [tuple(_plain(v) for v in r) for r in cur.fetchmany(max_rows)] if cur.description else []
        return cols, rows
    finally:
        raw.rollback()   # read-only anyway; never keep a transaction open
        raw.close()


def _plain(v):
    """Decimal('12.50') -> 12.5, so results read the same as SQLite's."""
    return float(v) if isinstance(v, Decimal) else v


def scalar(sql):
    return query(sql, max_rows=1)[1][0][0]


def tables():
    if dialect() == "sqlite":
        sql = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    else:
        sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
    return [r[0] for r in query(sql, max_rows=1000)[1]]


def columns(table):
    if dialect() == "sqlite":
        return [r[1] for r in query(f"PRAGMA table_info({table})", max_rows=1000)[1]]
    return [r[0] for r in query(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
        f"AND table_name = '{table}' ORDER BY ordinal_position", max_rows=1000)[1]]


def schema():
    """Table and column names, as text the LLM can read."""
    lines = [f"{t}({', '.join(columns(t))})" for t in tables()]
    lines.append(SQLITE_HINT if dialect() == "sqlite" else POSTGRES_HINT)
    return "\n".join(lines)


def exists():
    """True when the database is reachable and has at least one table."""
    try:
        if dialect() == "sqlite":
            import os
            if not os.path.exists(sqlite_path()):
                return False
        return bool(tables())
    except Exception:
        return False


def ping():
    query("SELECT 1")
    return True
