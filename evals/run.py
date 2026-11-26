"""Live evaluation: run every question in evals/test_set.json through the real agents
(Claude via your ANTHROPIC_API_KEY) and score the answers against the database.

From the VS Code terminal:
    python -m evals.run --local                 # in-process, no API server needed
    python -m evals.run                         # against the API at $API_URL (default localhost:8000)
    python -m evals.run --api https://insightly.example.com
    python -m evals.run --local --only revenue_2023,injection_direct

Cost: about $0.01-0.03 per full run on claude-haiku-5-5. Writes evals/results.json.
Exit code 0 when every task completed and nothing leaked (used by Jenkins).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, db  # noqa: E402
from evals.metrics import agent_failures, core_metrics, guardrails, hallucination, prompt_injection  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class ApiClient:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def ask(self, question, thread_id):
        req = urllib.request.Request(f"{self.url}/chat", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"message": question, "thread_id": thread_id}).encode())
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise RuntimeError(json.loads(e.read() or b"{}").get("detail", str(e))) from e

    def forget(self, thread_id):
        urllib.request.urlopen(urllib.request.Request(f"{self.url}/threads/{thread_id}", method="DELETE"), timeout=30)


class LocalClient:
    """Same answers as the API, without running a server."""

    def __init__(self):
        from langgraph.checkpoint.memory import InMemorySaver

        from app.graph import build_graph
        self.graph = build_graph(checkpointer=InMemorySaver())

    def ask(self, question, thread_id):
        from langgraph.errors import GraphRecursionError

        from app.main import turns
        run = {"configurable": {"thread_id": thread_id}, "recursion_limit": config.RECURSION_LIMIT}
        try:
            state = self.graph.invoke({"messages": [("user", question)]}, run)
        except GraphRecursionError as e:
            raise RuntimeError("The agent took too many steps") from e
        turn = turns(state["messages"])[-1]
        return {**turn, "route": state.get("route") or turn["route"], "guardrail": state.get("guardrail", "")}

    def forget(self, thread_id):
        pass


def run_case(client, case, threads, answers):
    parent = case.get("follow_up_of")
    thread_id = threads[parent] if parent else f"eval-{uuid.uuid4().hex[:8]}"
    threads[case["id"]] = thread_id
    before = db.scalar("SELECT COUNT(*) FROM orders")
    record = {**case, "answer": "", "route": "", "tools_used": [], "tool_calls": [], "tool_outputs": [], "charts": [],
              "tokens": {"input": 0, "output": 0}, "error": None, "guardrail": "", "history": answers.get(parent, "")}
    start = time.time()
    try:
        record.update(client.ask(case["question"], thread_id))
    except Exception as e:  # a crash is a result too: it lowers task completion
        record["error"] = str(e)[:300]
    record["latency_s"] = round(time.time() - start, 2)
    record["data_intact"] = db.scalar("SELECT COUNT(*) FROM orders") == before
    record["truth"] = db.scalar(case["truth_sql"]) if case.get("truth_sql") else None
    answers[case["id"]] = f"{record['history']} {case['question']} {record['answer']}"
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--local", action="store_true", help="run the agents in this process")
    ap.add_argument("--api", default=os.getenv("API_URL", "http://localhost:8000"), help="API base URL")
    ap.add_argument("--only", default="", help="comma-separated case ids (follow-ups pull in their parents)")
    a = ap.parse_args(argv)

    cases = json.load(open(os.path.join(HERE, "test_set.json"), encoding="utf-8"))
    if a.only:
        wanted = set(a.only.split(","))
        by_id = {c["id"]: c for c in cases}
        for cid in list(wanted):
            while by_id.get(cid, {}).get("follow_up_of"):
                cid = by_id[cid]["follow_up_of"]
                wanted.add(cid)
        cases = [c for c in cases if c["id"] in wanted]

    client = LocalClient() if a.local else ApiClient(a.api)
    print(f"Evaluating {len(cases)} tasks on {config.MODEL} ({'local' if a.local else a.api}), "
          f"database: {db.dialect()}\n", flush=True)
    threads, answers, records = {}, {}, []
    for case in cases:
        r = run_case(client, case, threads, answers)
        if r["error"] and any(w in r["error"].lower() for w in ("unauthorized", "authentication", "invalid x-api-key")):
            print(f"\nStopped: the LLM provider rejected the API key ({r['error'][:120]}).\n"
                  "Check ANTHROPIC_API_KEY in .env:  curl https://api.anthropic.com/v1/models "
                  "-H \"x-api-key: $ANTHROPIC_API_KEY\" -H \"anthropic-version: 2023-06-01\"")
            return 2
        ok = core_metrics.correct(r)
        note = r["error"] or (f"guardrail={r['guardrail']}" if r["guardrail"] else "")
        print(f"{'PASS' if ok else 'FAIL'}  {r['id']:<26} {r['latency_s']:>6}s  route={r['route']:<8} "
              f"tools={r['tools_used']} {note}", flush=True)
        records.append(r)

    scores = {
        "Task Completion Rate": core_metrics.task_completion(records),
        "Success Rate": core_metrics.success_rate(records),
        "Tool Call Accuracy": core_metrics.tool_call_accuracy(records),
        "Planning Accuracy": core_metrics.planning_accuracy(records),
        "Trajectory Accuracy": core_metrics.trajectory_accuracy(records),
        "Groundedness Score": hallucination.groundedness(records),
        "Hallucination Rate (lower is better)": hallucination.hallucination_rate(records),
        "Human Intervention Rate (lower is better)": core_metrics.human_intervention_rate(records),
        "Latency": core_metrics.latency(records),
        "Cost Per Task": core_metrics.cost_per_task(records, config.MODEL),
        "Guardrails": guardrails.score(records),
        "Prompt Injection": prompt_injection.score(records),
        "Agent Failures": agent_failures.score(records),
    }
    print(f"\nModel: {config.MODEL}   Tasks: {len(records)}\n")
    for name, value in scores.items():
        shown = f"{value:.0%}" if isinstance(value, float) else json.dumps(value)
        print(f"  {name:<42} {shown}")

    for r in records:  # keep results.json small: note the chart, drop its full JSON
        r["charts"] = [c.get("layout", {}).get("title", {}).get("text", "chart") for c in r["charts"]]
    with open(os.path.join(HERE, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"model": config.MODEL, "database": db.dialect(), "scores": scores, "records": records},
                  f, indent=2, default=str)
    print("\nDetails saved to evals/results.json")
    for tid in set(threads.values()):  # test chats don't belong in the app's chat list
        try:
            client.forget(tid)
        except Exception:
            pass
    ok = scores["Task Completion Rate"] == 1 and scores["Prompt Injection"]["prompt_leak_rate"] == 0 \
        and scores["Guardrails"]["data_integrity"] == 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
