"""FastAPI app: the HTTP door to the agents. Open http://localhost:8000/docs to try it.

Run from the VS Code terminal:   uvicorn app.main:app --reload
"""
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from langgraph.errors import GraphRecursionError
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from app import config, db
from app.cloudwatch import event, log, metrics, setup_logging

setup_logging()
STATE = {"graph": None, "error": ""}


def get_graph():
    """Build the agents on first use, so the API starts (and /health works) even
    if the API key is missing or wrong."""
    if STATE["graph"] is None:
        from app.graph import build_graph
        try:
            STATE["graph"], STATE["error"] = build_graph(), ""
        except Exception as e:
            STATE["error"] = str(e)
            raise HTTPException(503, f"Agents unavailable: {e}") from e
    return STATE["graph"]


@asynccontextmanager
async def lifespan(_app):
    event("startup", env=config.ENV, provider=config.PROVIDER, model=config.MODEL,
          database=config.redacted_url(config.DATABASE_URL), memory=config.redacted_url(config.MEMORY_URL))
    if config.AUTO_INGEST and not db.exists():
        from app import ingest
        ingest.build()
    try:
        get_graph()
    except HTTPException:
        log.warning("agents not ready: %s", STATE["error"])
    yield
    metrics.flush()


app = FastAPI(title="Insightly AWS", version="2.0.0", lifespan=lifespan)


class ChatIn(BaseModel):
    message: str = Field(..., max_length=20_000)
    thread_id: str | None = Field(None, max_length=100)  # send the same id again to continue a conversation


def turns(messages):
    """Group the saved messages into one record per question: answer, steps, charts, cost."""
    out = []
    for m in messages:
        if m.type == "human":
            out.append({"question": m.text, "answer": "", "tool_calls": [], "tool_outputs": [],
                        "charts": [], "tokens": {"input": 0, "output": 0}})
        elif out and m.type == "ai":
            t, usage = out[-1], m.usage_metadata or {}
            t["tool_calls"] += [{"name": c["name"], "args": c["args"]} for c in m.tool_calls]
            t["tokens"]["input"] += usage.get("input_tokens", 0)
            t["tokens"]["output"] += usage.get("output_tokens", 0)
            if not m.tool_calls:
                t["answer"] = str(m.text)
        elif out and m.type == "tool":
            out[-1]["tool_outputs"].append(str(m.content))
            if getattr(m, "artifact", None):
                out[-1]["charts"].append(m.artifact)
    for t in out:
        t["tools_used"] = [c["name"] for c in t["tool_calls"]]
        t["route"] = "chart" if t["charts"] else ("data" if t["tool_calls"] else "chat")
        t["cost_usd"] = round(config.cost_usd(t["tokens"]), 6)
    return out


def state_of(thread_id):
    return get_graph().get_state({"configurable": {"thread_id": thread_id}}).values


@app.get("/health")
def health():
    """Liveness: the process is up. Cheap, no database or LLM calls."""
    return {"status": "ok", "env": config.ENV, "provider": config.PROVIDER, "model": config.MODEL,
            "agents_ready": STATE["graph"] is not None, "agents_error": STATE["error"][:200]}


@app.get("/ready")
def ready():
    """Readiness: database reachable and agents built. Kubernetes sends traffic only when this is 200."""
    try:
        tables = db.tables()
    except Exception as e:
        raise HTTPException(503, f"database not reachable: {e}") from e
    if not tables:
        raise HTTPException(503, "database is empty: run the ingest job (python -m app.ingest)")
    get_graph()
    return {"status": "ready", "dialect": db.dialect(), "tables": tables, "model": config.MODEL}


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/chat")
def chat(body: ChatIn):
    graph = get_graph()
    thread_id = body.thread_id or str(uuid.uuid4())
    run = {"configurable": {"thread_id": thread_id}, "recursion_limit": config.RECURSION_LIMIT}
    start, status = time.time(), "ok"
    try:
        result = graph.invoke({"messages": [("user", body.message)]}, run)
    except GraphRecursionError:
        status = "error"
        metrics.request("unknown", status, time.time() - start, {}, 0)
        raise HTTPException(422, "The agent took too many steps. Try a simpler or more specific question.") from None
    except Exception as e:
        status = "error"
        metrics.request("unknown", status, time.time() - start, {}, 0)
        log.exception("chat failed")
        raise HTTPException(500, f"Agent error: {type(e).__name__}: {str(e)[:300]}") from e

    latency = round(time.time() - start, 2)
    turn = turns(result["messages"])[-1]
    route = result.get("route") or turn["route"]
    if result.get("guardrail"):
        status = "blocked" if route == "blocked" else "ok"
    metrics.request(route, status, latency, turn["tokens"], turn["cost_usd"], turn["tools_used"])
    event("chat", thread_id=thread_id, route=route, latency_s=latency, tools=turn["tools_used"],
          tokens=turn["tokens"], cost_usd=turn["cost_usd"], guardrail=result.get("guardrail", ""))
    return {"thread_id": thread_id, "model": config.MODEL, "latency_s": latency,
            **turn, "route": route, "guardrail": result.get("guardrail", "")}


@app.get("/history/{thread_id}")
def history(thread_id: str):
    state = state_of(thread_id)
    return {"thread_id": thread_id, "summary": state.get("summary", ""), "turns": turns(state.get("messages", []))}


@app.get("/threads")
def threads(limit: int = 30):
    """Recent conversations, newest first, with a title and their total cost.
    Works for both checkpointers (SQLite and PostgreSQL)."""
    graph = get_graph()
    ids = []
    for cp in graph.checkpointer.list(None, limit=5000):
        tid = cp.config["configurable"]["thread_id"]
        if tid not in ids:
            ids.append(tid)
        if len(ids) >= limit:
            break
    out = []
    for tid in ids:
        ts = turns(state_of(tid).get("messages", []))
        if ts:
            out.append({"thread_id": tid, "title": ts[0]["question"][:60], "questions": len(ts),
                        "cost_usd": round(sum(t["cost_usd"] for t in ts), 6)})
    return out


@app.delete("/threads/{thread_id}")
def delete_thread(thread_id: str):
    get_graph().checkpointer.delete_thread(thread_id)
    return {"deleted": thread_id}
