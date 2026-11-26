"""Hallucination: does every number in the answer come from evidence?

Evidence = this turn's tool outputs + the question + earlier answers in the same thread.
Groundedness = share of numbers backed by evidence (an answer with no numbers scores 1).
Hallucination rate = share of answers with at least one unsupported number (lower is better).
"""
import re

NUM = re.compile(r"(-?\d[\d,]*\.?\d*)\s*(%|k\b|m\b|million|bn|b\b|billion)?", re.I)
SCALE = {"k": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
GRADED = ("data", "chart", "no_data", "recall")


def strip_code(text):
    """Drop SQL / code; we only judge the sentence."""
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    return re.sub(r"`[^`]*`", " ", text)


def numbers(text, skip_small=True):
    """[(value, tolerance)] for every number written in the text."""
    out = []
    for raw, unit in NUM.findall(strip_code(text)):
        raw = raw.rstrip(".,")
        if not raw or raw == "-":
            continue
        value = float(raw.replace(",", ""))
        scale = SCALE.get((unit or "").lower(), 1)
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if skip_small and scale == 1 and not unit and decimals == 0 and abs(value) <= 12:
            continue  # "top 3", "2 sentences": not claims
        out.append((value * scale, 0.5 * 10 ** -decimals * scale))
    return out


def close(a, tol, b):
    """True if b rounds to a (or is within 1%)."""
    return abs(a - b) <= tol + 1e-9 or (b != 0 and abs(a - b) / abs(b) <= 0.01)


def unsupported(r):
    evidence = [v for v, _ in numbers(" ".join(r["tool_outputs"]) + " " + r["question"] + " " + r.get("history", ""),
                                      skip_small=False)]
    claims = numbers(r["answer"])
    return [v for v, tol in claims if not any(close(v, tol, e) for e in evidence)], len(claims)


def case_groundedness(r):
    bad, total = unsupported(r)
    return 1.0 if total == 0 else (total - len(bad)) / total


def _graded(records):
    return [r for r in records if r["type"] in GRADED] or records


def groundedness(records):
    rs = _graded(records)
    return sum(case_groundedness(r) for r in rs) / len(rs)


def hallucination_rate(records):
    rs = _graded(records)
    return sum(bool(unsupported(r)[0]) for r in rs) / len(rs)
