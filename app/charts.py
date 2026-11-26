"""Turn query results into a clean, analyst-style Plotly chart.

build() returns (figure as a dict, short text summary). The figure goes to the
app; the summary goes to the LLM so its insights use real numbers only.
"""
import json

import pandas as pd
import plotly.express as px

COLORS = ["#C96442", "#3B6EA8", "#5A8F5A", "#D4A24C", "#8C6BB1", "#4C9A9A", "#B5577B", "#7A7A72"]
FONT = "Inter, Segoe UI, Helvetica Neue, Arial, sans-serif"
px.defaults.color_discrete_sequence = COLORS
TIME_WORDS = ("date", "day", "week", "month", "year", "quarter", "period", "time")
KINDS = ("bar", "line", "area", "pie", "scatter")


def short(v):
    """1234567 -> 1.23M, 0.4567 -> 0.46"""
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= size:
            return f"{v / size:.2f}{unit}"
    return f"{v:,.2f}"


def is_time(df, x):
    return any(w in x.lower() for w in TIME_WORDS) or bool(df[x].astype(str).str.match(r"^\d{4}(-\d{2})?").all())


def build(cols, rows, kind, x, y, title, color=""):
    if kind not in KINDS:
        raise ValueError(f"unknown chart type '{kind}'. Use one of {KINDS}")
    df = pd.DataFrame([tuple(r) for r in rows], columns=list(cols))
    for c in [x, y] + ([color] if color else []):
        if c not in df.columns:
            raise ValueError(f"column '{c}' is not in the result. Columns are: {list(cols)}")
    df[y] = pd.to_numeric(df[y], errors="coerce")
    df = df.dropna(subset=[y])
    if df.empty:
        raise ValueError("the query returned no numeric rows, so there is nothing to draw")
    color = color or None
    time_axis = is_time(df, x)

    if kind in ("line", "area") or (kind == "bar" and time_axis):
        df = df.sort_values(x)
        df[x] = df[x].astype(str)
    if kind == "bar":
        horizontal = not time_axis and not color and (len(df) > 6 or df[x].astype(str).str.len().mean() > 10)
        if horizontal:
            df = df.sort_values(y)  # biggest bar on top
            fig = px.bar(df, x=y, y=x, orientation="h", text=y)
            fig.update_traces(texttemplate="%{x:.3s}")
        else:
            if not time_axis and not color:
                df = df.sort_values(y, ascending=False)
            fig = px.bar(df, x=x, y=y, color=color, text=y)
            fig.update_traces(texttemplate="%{y:.3s}")
        fig.update_traces(textposition="outside", cliponaxis=False, marker_line_width=0)
    elif kind == "line":
        fig = px.line(df, x=x, y=y, color=color, markers=True)
        fig.update_traces(line_width=3, marker_size=7)
    elif kind == "area":
        fig = px.area(df, x=x, y=y, color=color)
    elif kind == "pie":
        fig = px.pie(df, names=x, values=y, hole=0.5)
        fig.update_traces(textinfo="percent+label", textposition="outside", sort=True)
    else:  # scatter
        fig = px.scatter(df, x=x, y=y, color=color)
        fig.update_traces(marker_size=10, marker_opacity=0.75)

    if kind in ("line", "area") and not color:  # call out the peak, like an analyst would
        top = df.loc[df[y].idxmax()]
        fig.add_annotation(x=top[x], y=top[y], text=f"Peak: {short(top[y])}", showarrow=True,
                           arrowhead=0, ay=-36, font_color=COLORS[0], bgcolor="white")

    fig.update_layout(
        template="plotly_white", colorway=COLORS, font_family=FONT, font_color="#1F1E1D",
        title={"text": f"<b>{title}</b><br><sup style='color:#7A7A72'>{len(df)} rows · source: shop database</sup>",
               "x": 0.01, "font_size": 18},
        margin={"l": 10, "r": 30, "t": 80, "b": 10}, height=440,
        legend={"orientation": "h", "y": -0.15, "title": None},
        hoverlabel={"bgcolor": "white", "font_family": FONT},
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis_title=None if kind != "scatter" else x, yaxis_title=None if kind != "scatter" else y,
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#EEECE6", zeroline=False)
    return json.loads(fig.to_json()), summarize(df, kind, x, y, time_axis)


def summarize(df, kind, x, y, time_axis):
    """Numbers the LLM may quote. It must not invent others."""
    hi, lo = df.loc[df[y].idxmax()], df.loc[df[y].idxmin()]
    lines = [f"Chart drawn ({kind}, {len(df)} rows).",
             f"Total {y}: {df[y].sum():,.2f}",
             f"Highest: {hi[x]} = {hi[y]:,.2f}",
             f"Lowest: {lo[x]} = {lo[y]:,.2f}"]
    if time_axis and len(df) > 1 and df[y].iloc[0]:
        first, last = df[y].iloc[0], df[y].iloc[-1]
        lines.append(f"From {df[x].iloc[0]} to {df[x].iloc[-1]}: {first:,.2f} -> {last:,.2f} "
                     f"({(last - first) / abs(first) * 100:+.1f}%)")
    if kind == "pie":
        share = df[y] / df[y].sum() * 100
        lines.append(f"Shares below are of the {len(df)} slices shown only, not of all data:")
        lines += [f"Share of {r[x]}: {s:.1f}%" for (_, r), s in zip(df.iterrows(), share, strict=False)][:8]
    lines.append("First rows: " + df.head(12).to_csv(index=False).replace("\n", " | "))
    return "\n".join(lines)
