"""Insightly: the web app. Chats, charts, cost and the steps behind every answer.

Run from the VS Code terminal (with the API running):   streamlit run ui/app.py
Set API_URL if the API is not on http://localhost:8000.
"""
import os
import time

import plotly.graph_objects as go
import requests
import streamlit as st

API = os.getenv("API_URL", "http://localhost:8000")

SUGGESTIONS = [
    "Show monthly net revenue for 2023 as a line chart",
    "What was net revenue in 2023?",
    "Which 10 brands made the most gross profit? Show a bar chart",
    "What is our return rate?",
    "Chart the share of orders by country",
    "What is the session conversion rate?",
]
ROUTE_LABEL = {"data": "Analyst agent", "chart": "Chart agent", "chat": "Assistant", "blocked": "Guardrail"}

st.set_page_config(page_title="Insightly", page_icon=":material/insights:", layout="wide")
st.markdown("""
<style>
  .block-container {padding-top: 2rem; max-width: 1100px;}
  h1, h2, h3 {font-family: Georgia, 'Times New Roman', serif; font-weight: 500;}
  [data-testid="stMetricValue"] {font-size: 1.5rem;}
  [data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #E8E6DC; border-radius: 12px; padding: 12px 16px;}
  .meta {color: #7A7A72; font-size: 0.82rem; margin-top: 4px;}
  .pill {display: inline-block; background: #F0EEE6; color: #C96442; border-radius: 99px;
         padding: 1px 10px; font-size: 0.78rem; font-weight: 600; margin-right: 6px;}
  .pill.guard {background: #FBEAEA; color: #A33A3A;}
  section[data-testid="stSidebar"] .stButton button {text-align: left; justify-content: flex-start;}
</style>""", unsafe_allow_html=True)


# ---------- talking to the API ----------
def api(method, path, **kw):
    try:
        r = requests.request(method, f"{API}{path}", timeout=kw.pop("timeout", 30), **kw)
        if r.status_code >= 400:
            is_json = r.headers.get("content-type", "").startswith("application/json")
            detail = r.json().get("detail", r.text) if is_json else r.text
            st.error(f"API error {r.status_code}: {detail}")
            return None
        return r.json()
    except requests.RequestException as e:
        st.error(f"Can't reach the API at {API}: {e}")
        return None


@st.cache_data(ttl=30)
def ready():
    try:
        r = requests.get(f"{API}/ready", timeout=10)
        return r.json() if r.ok else {"error": r.json().get("detail", r.text)}
    except requests.RequestException as e:
        return {"error": str(e)}


def open_thread(thread_id):
    st.session_state.thread_id = thread_id
    st.query_params["thread"] = thread_id
    data = api("GET", f"/history/{thread_id}") or {"turns": []}
    st.session_state.turns = data["turns"]


def new_chat():
    st.session_state.thread_id = None
    st.session_state.turns = []
    st.query_params.clear()


if "turns" not in st.session_state:
    st.session_state.turns, st.session_state.thread_id = [], None
    if "thread" in st.query_params:  # survive a browser refresh
        open_thread(st.query_params["thread"])


# ---------- sidebar: conversations + status ----------
with st.sidebar:
    st.markdown("## Insightly")
    st.caption("Ask your shop data anything")
    st.button("＋  New chat", width="stretch", type="primary", on_click=new_chat)

    st.markdown("#### Conversations")
    for t in api("GET", "/threads") or []:
        active = t["thread_id"] == st.session_state.thread_id
        label = f"{'▸ ' if active else ''}{t['title']}"
        if st.button(label, key=t["thread_id"], width="stretch",
                     help=f"{t['questions']} questions · ${t['cost_usd']:.4f}"):
            open_thread(t["thread_id"])
            st.rerun()

    st.divider()
    h = ready()
    if "error" in h:
        st.markdown(f"**API not ready**  \n{h['error']}")
    else:
        st.markdown(f"**API online**  \nModel: `{h['model']}`  \nDatabase: {h['dialect']} · {len(h['tables'])} tables")
        with st.expander("Tables"):
            st.write(", ".join(h["tables"]))
    if st.session_state.thread_id:
        if st.button("Delete this chat", width="stretch"):
            api("DELETE", f"/threads/{st.session_state.thread_id}")
            new_chat()
            st.rerun()


# ---------- header + conversation stats ----------
turns = st.session_state.turns
st.title("Insightly")
st.caption("Your AI data analyst · answers come from real SQL on the shop database")

if turns:
    tokens = sum(t["tokens"]["input"] + t["tokens"]["output"] for t in turns)
    timed = [t["latency_s"] for t in turns if t.get("latency_s")]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Questions", len(turns))
    c2.metric("API cost (this chat)", f"${sum(t['cost_usd'] for t in turns):.4f}")
    c3.metric("Tokens used", f"{tokens:,}")
    c4.metric("Avg response", f"{sum(timed) / len(timed):.1f}s" if timed else "–")


# ---------- one answer ----------
def show_turn(t, i):
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(t["question"])
    with st.chat_message("assistant", avatar=":material/insights:"):
        st.markdown(t["answer"] or "_No answer_")
        for j, fig in enumerate(t["charts"]):
            st.plotly_chart(go.Figure(fig), width="stretch", key=f"chart-{i}-{j}", config={"displaylogo": False})
        bits = []
        if t.get("route"):
            bits.append(f"<span class='pill'>{ROUTE_LABEL.get(t['route'], t['route'])}</span>")
        if t.get("guardrail"):
            bits.append(f"<span class='pill guard'>guardrail: {t['guardrail']}</span>")
        bits.append(f"{len(t['tool_calls'])} steps · {t['tokens']['input'] + t['tokens']['output']:,} tokens"
                    f" · ${t['cost_usd']:.4f}" + (f" · {t['latency_s']:.1f}s" if t.get("latency_s") else ""))
        st.markdown(f"<div class='meta'>{''.join(bits)}</div>", unsafe_allow_html=True)
        if t["tool_calls"]:
            with st.expander("How I got this answer"):
                for n, (call, out) in enumerate(zip(t["tool_calls"], t["tool_outputs"], strict=False), 1):
                    st.markdown(f"**{n}. `{call['name']}`**")
                    if "sql" in call["args"]:
                        st.code(call["args"]["sql"], language="sql")
                    elif call["args"]:
                        st.caption(", ".join(f"{k}: {v}" for k, v in call["args"].items()))
                    st.caption(out[:400] + ("…" if len(out) > 400 else ""))


for i, t in enumerate(turns):
    show_turn(t, i)


# ---------- empty state: suggestions ----------
picked = None
if not turns:
    st.markdown("### What would you like to know?")
    cols = st.columns(3)
    for k, text in enumerate(SUGGESTIONS):
        if cols[k % 3].button(text, key=f"s{k}", width="stretch"):
            picked = text


# ---------- ask ----------
question = st.chat_input("Ask about revenue, orders, customers… or ask for a chart") or picked
if question:
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(question)
    with st.chat_message("assistant", avatar=":material/insights:"):
        with st.spinner("Analysing your data…"):
            start = time.time()
            answer = api("POST", "/chat", timeout=300,
                         json={"message": question, "thread_id": st.session_state.thread_id})
    if answer:
        st.session_state.thread_id = answer["thread_id"]
        st.query_params["thread"] = answer["thread_id"]
        st.session_state.turns.append(answer)
        st.rerun()
