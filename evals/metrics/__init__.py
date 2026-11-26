"""Eval metrics. Each function takes the list of eval records and returns a number
(0..1, seconds or dollars) or a small dict.

core_metrics      task completion, success, tool calls, planning, trajectory, latency, cost
hallucination     groundedness + hallucination rate (numbers with no evidence behind them)
guardrails        refusals of unsafe requests, PII leaks, false blocks of normal questions
prompt_injection  attacks resisted, system-prompt leaks
agent_failures    why each failed task failed (a breakdown, for debugging)
"""
