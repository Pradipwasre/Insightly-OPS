"""Shared test setup. Tests need no API key and no real data:

* a small generated shop (same columns as the real one) is loaded into a temp database,
* a scripted fake LLM replaces Claude, so the full agent graph runs offline.

Run from the VS Code terminal:   pytest -q
Against PostgreSQL (like RDS):   TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/test pytest -q
"""
import os
import sys
import tempfile

# Must happen before any `app` import: config reads the environment once.
_TMP = tempfile.mkdtemp(prefix="insightly-test-")
os.environ.update({
    "APP_ENV": "test",
    "DATA_DIR": _TMP,
    "CSV_DIR": os.path.join(_TMP, "no-csvs"),
    "DATABASE_URL": os.getenv("TEST_DATABASE_URL", f"sqlite:///{os.path.join(_TMP, 'shop.db')}"),
    "MEMORY_URL": f"sqlite:///{os.path.join(_TMP, 'memory.db')}",
    "AUTO_INGEST": "false",
    "CLOUDWATCH_ENABLED": "false",
    "ANTHROPIC_API_KEY": "",
    "LLM_PROVIDER": "anthropic",
})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from tests.fakes import ScriptedLLM, tool_call  # noqa: E402,F401


@pytest.fixture(scope="session", autouse=True)
def demo_db():
    from app import ingest
    ingest.build(force=True, demo=True)
    yield


@pytest.fixture
def llm():
    return ScriptedLLM()


@pytest.fixture
def make_graph():
    """make_graph(llm) -> compiled agent graph with in-memory chat memory."""
    from langgraph.checkpoint.memory import InMemorySaver

    from app.graph import build_graph

    def _make(fake):
        return build_graph(checkpointer=InMemorySaver(), llm=fake, fast_llm=fake)
    return _make


@pytest.fixture
def ask(make_graph):
    """ask(llm, question, thread="t1") -> final graph state."""
    graphs = {}

    def _ask(fake, question, thread="t1"):
        graph = graphs.setdefault(id(fake), make_graph(fake))
        return graph.invoke({"messages": [("user", question)]},
                            {"configurable": {"thread_id": thread}, "recursion_limit": 25})
    return _ask
