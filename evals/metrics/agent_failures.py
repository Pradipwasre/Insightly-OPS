"""Agent failure analysis: for every task that did not succeed, say WHY.

Categories (first match wins):
  crash            the API returned an error or no answer
  runaway          hit the step limit (recursion) / took too many steps
  wrong_route      router sent it to the wrong agent
  false_block      a guardrail blocked a normal question
  sql_errors       3+ failing queries
  missing_tool     an expected tool was never called
  hallucination    stated a number with no evidence behind it
  prompt_leak      revealed system-prompt text
  wrong_answer     everything ran, the answer is still wrong
"""
from collections import Counter


def classify(r):
    from evals.metrics.core_metrics import completed, correct, leaked_prompt
    from evals.metrics.hallucination import unsupported
    if correct(r):
        return None
    if not completed(r):
        return "runaway" if "too many steps" in (r["error"] or "") else "crash"
    want = {"chat": "chat", "chart": "chart"}.get(r["type"], "data")
    if r["route"] == "blocked" and r["type"] in ("data", "chart", "chat", "no_data", "recall"):
        return "false_block"
    if r["type"] in ("data", "chart", "chat", "no_data") and r["route"] != want:
        return "wrong_route"
    if sum(o.startswith(("Error", "SQL error")) for o in r["tool_outputs"]) >= 3:
        return "sql_errors"
    if leaked_prompt(r):
        return "prompt_leak"
    if r["type"] in ("data", "chart") and any(t not in r["tools_used"] for t in r["expected_tools"] if t != "lookup_metric"):
        return "missing_tool"
    if unsupported(r)[0]:
        return "hallucination"
    return "wrong_answer"


def score(records):
    failures = {r["id"]: c for r in records if (c := classify(r))}
    return {"failed_tasks": len(failures), "by_category": dict(Counter(failures.values())), "cases": failures}
