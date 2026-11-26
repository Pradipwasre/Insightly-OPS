"""Business glossary + a tiny retriever. This is the "R" in RAG.

The agent looks up the official definition of a metric before writing SQL, so
"revenue" always means the same thing. The SQL is portable: it runs unchanged on
SQLite (local) and PostgreSQL (RDS). Real projects use embeddings and a vector
store; keyword overlap keeps the idea visible in a few lines.
Order statuses in the data: Complete, Shipped, Processing, Returned, Cancelled.
"""
import re

METRICS = [
    {"name": "Net revenue", "words": "revenue sales income money turnover",
     "sql": "SUM(sale_price) FROM order_items WHERE status NOT IN ('Cancelled','Returned')"},
    {"name": "Gross profit", "words": "profit earnings margin",
     "sql": "SUM(oi.sale_price - p.cost) FROM order_items oi JOIN products p ON p.id = oi.product_id "
            "WHERE oi.status NOT IN ('Cancelled','Returned')"},
    {"name": "Order count", "words": "orders number volume how many",
     "sql": "COUNT(*) FROM orders  (all statuses)"},
    {"name": "Average order value", "words": "aov average basket order value",
     "sql": "net revenue / COUNT(DISTINCT order_id), both from order_items WHERE status NOT IN ('Cancelled','Returned')"},
    {"name": "Return rate", "words": "returns returned return rate",
     "sql": "100.0 * SUM(CASE WHEN status = 'Returned' THEN 1 ELSE 0 END) / COUNT(*) FROM orders"},
    {"name": "Cancellation rate", "words": "cancel cancelled cancellation",
     "sql": "100.0 * SUM(CASE WHEN status = 'Cancelled' THEN 1 ELSE 0 END) / COUNT(*) FROM orders"},
    {"name": "Active customers", "words": "customers buyers active users who ordered",
     "sql": "COUNT(DISTINCT user_id) FROM orders"},
    {"name": "Conversion rate", "words": "conversion convert funnel sessions purchase",
     "sql": "100.0 * COUNT(DISTINCT CASE WHEN event_type = 'purchase' THEN session_id END) "
            "/ COUNT(DISTINCT session_id) FROM events"},
]


def tokens(text):
    return set(re.findall(r"[a-z]+", text.lower()))


def search(question, k=2):
    q = tokens(question)
    scored = [(len(q & tokens(m["name"] + " " + m["words"])), m) for m in METRICS]
    return [m for score, m in sorted(scored, key=lambda x: -x[0]) if score > 0][:k]
