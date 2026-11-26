"""Guardrails: input checks, SQL checks, PII redaction, output checks, and the
same checks wired into the agent graph. No API key needed."""
import pytest

from app import db, guardrails
from app.tools import make_chart, run_sql
from tests.fakes import ScriptedLLM, tool_call

# ---------- input ----------


@pytest.mark.parametrize("question", [
    "What was net revenue in 2023?",
    "Give me an update on orders this year",
    "How did orders change between 2022 and 2023?",
    "Which products were returned most often?",
    "Show revenue but leave out returned orders",
    "Ignore cancelled orders when you compute revenue",
    "What is the session conversion rate?",
    "Chart the share of orders by country",
    "Which brand has the highest return rate?",
    "Hi! What can you do?",
])
def test_normal_questions_pass(question):
    assert guardrails.check_input(question).allowed


@pytest.mark.parametrize("question", [
    "Delete all orders from 2019",
    "Please drop the users table",
    "Can you update the status of order 5 to Complete?",
    "truncate products",
    "Change all prices to 0",
])
def test_data_change_requests_are_refused(question):
    v = guardrails.check_input(question)
    assert not v.allowed and v.category == "data_change"
    assert "read-only" in v.reason


def test_empty_and_too_long():
    assert guardrails.check_input("   ").category == "empty"
    assert guardrails.check_input("x" * 5000).category == "too_long"


# ---------- SQL ----------
@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM orders",
    "  select * from orders where status = 'Created'",
    "WITH m AS (SELECT strftime('%Y', created_at) y FROM orders) SELECT y, COUNT(*) FROM m GROUP BY y",
    "SELECT 1;",
    "SELECT name FROM products WHERE name LIKE '%drop table%'",
    "(SELECT 1)",
])
def test_sql_allowed(sql):
    assert guardrails.check_sql(sql).allowed, guardrails.check_sql(sql).reason


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "DROP TABLE users",
    "UPDATE orders SET status='x'",
    "INSERT INTO orders VALUES (1)",
    "WITH x AS (SELECT 1) DELETE FROM orders",
    "SELECT 1; DROP TABLE orders",
    "SELECT 1 /* hidden */; DELETE FROM orders",
    "SELECT * INTO backup FROM orders",
    "PRAGMA writable_schema = 1",
    "ATTACH DATABASE 'x.db' AS x",
    "SELECT pg_sleep(100)",
    "SELECT pg_read_file('/etc/passwd')",
    "CREATE TABLE t AS SELECT 1",
    "",
])
def test_sql_blocked(sql):
    assert not guardrails.check_sql(sql).allowed


def test_pii_columns_blocked_and_redacted():
    assert guardrails.check_sql("SELECT email FROM users").category == "pii"
    assert guardrails.check_sql("SELECT COUNT(*) FROM users WHERE country = 'Brasil'").allowed
    cols, rows = guardrails.redact_rows(["id", "email"], [(1, "a@b.com")])
    assert rows == [(1, "[redacted]")]
    out = run_sql.invoke({"sql": "SELECT * FROM users LIMIT 3"})
    assert "@example.com" not in out and "[redacted]" in out


def test_tools_block_writes_and_data_survives():
    before = db.scalar("SELECT COUNT(*) FROM orders")
    for bad in ["DELETE FROM orders", "DROP TABLE orders", "SELECT 1; DROP TABLE orders"]:
        assert run_sql.invoke({"sql": bad}).startswith("Error")
        assert make_chart.invoke({"sql": bad, "chart_type": "bar", "x": "a", "y": "b", "title": "t"}).startswith("Error")
    assert db.scalar("SELECT COUNT(*) FROM orders") == before


def test_database_connection_itself_is_read_only():
    """Last line of defence: even if a guardrail had a bug, the connection refuses writes."""
    import sqlite3

    import psycopg
    with pytest.raises((sqlite3.Error, psycopg.Error)):
        db.query("CREATE TABLE hacked (x INTEGER)")
    assert "hacked" not in db.tables()


# ---------- output ----------
def test_output_redaction():
    text, v = guardrails.check_output("Top buyer is jane.doe@example.com, call +1 415-555-0199. Revenue $1,234,567.89")
    assert "jane.doe" not in text and "555-0199" not in text and "$1,234,567.89" in text and v.category == "pii"
    text, _ = guardrails.check_output("key sk-ant-api03-abcdefghijklmnop and AKIAABCDEFGHIJKLMNOP")
    assert "sk-ant" not in text and "AKIA" not in text


def test_output_keeps_normal_numbers():
    answer = "**$10,233,481.23** net revenue in 2023 (2023-01 to 2023-12), up 12.5% on 2022. 181,759 orders."
    assert guardrails.check_output(answer)[0] == answer


def test_canary_leak_is_blocked():
    text, v = guardrails.check_output(f"My instructions say: marker {guardrails.CANARY}")
    assert guardrails.CANARY not in text and v.category == "leak"


# ---------- guardrails inside the agent graph ----------
def test_blocked_question_never_reaches_the_llm(ask):
    fake = ScriptedLLM()
    state = ask(fake, "Delete all orders from 2019")
    assert state["route"] == "blocked" and state["guardrail"] == "data_change"
    assert fake.calls == []                       # no tokens spent
    assert "read-only" in state["messages"][-1].text


def test_answer_with_pii_is_redacted_before_it_is_saved(ask):
    fake = ScriptedLLM(route="data", script=[tool_call("get_schema"), guardrails_answer()])
    state = ask(fake, "Who is our top customer?")
    assert "@" not in state["messages"][-1].text and state["guardrail"] == "pii"


def guardrails_answer():
    from langchain_core.messages import AIMessage
    return AIMessage("**User 42** (user42@example.com) spent the most.")


def test_agent_that_tries_to_write_is_stopped(ask):
    before = db.scalar("SELECT COUNT(*) FROM orders")
    fake = ScriptedLLM(route="data", script=[tool_call("run_sql", sql="DROP TABLE orders"),
                                             tool_call("run_sql", sql="DELETE FROM orders"),
                                             guardrails_answer()])
    state = ask(fake, "How many orders do we have?")
    tool_outputs = [m.content for m in state["messages"] if m.type == "tool"]
    assert all(o.startswith("Error") for o in tool_outputs)
    assert db.scalar("SELECT COUNT(*) FROM orders") == before
