"""图表工具：把 LLM 输出的「结构化图表配置」渲染成 Plotly 图表。

关键设计：绝不让 LLM 生成并执行 Python 绘图代码（那等于远程代码执行），
LLM 只负责产出 JSON 配置，由本模块做白名单校验后用确定性的代码渲染。
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import plotly.graph_objects as go

SUPPORTED_CHART_TYPES = {"bar", "line", "area", "pie", "scatter", "histogram", "table", "none"}
NUMERIC_HINTS = {"amount", "total", "sum", "avg", "count", "quantity", "price", "sales", "revenue"}


def _jsonify(value: Any) -> Any:
    """把 DuckDB 返回的 Decimal / date / datetime 递归转成 JSON 友好的类型。"""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    return value


def rows_to_records(columns: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    return [{col: _jsonify(row[i]) for i, col in enumerate(columns)} for row in rows]


def normalize_chart_config(
    raw: dict[str, Any] | None,
    columns: list[str],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """校验并补全 LLM 给出的图表配置，保证前端一定能渲染。"""
    cfg = dict(raw or {})
    chart_type = str(cfg.get("type") or "").lower()
    if chart_type not in SUPPORTED_CHART_TYPES:
        chart_type = _infer_chart_type(columns, records)

    x = cfg.get("x")
    y = cfg.get("y")
    if chart_type in {"bar", "line", "area", "pie", "scatter", "histogram"}:
        x = x if x in columns else _guess_x(columns, records)
        y = y if y in columns else _guess_y(columns, records, x)

    title = str(cfg.get("title") or "").strip()[:80] or "分析结果"
    return {
        "type": chart_type,
        "x": x if x in columns else None,
        "y": y if y in columns else None,
        "title": title,
        "x_label": str(cfg.get("x_label") or x or ""),
        "y_label": str(cfg.get("y_label") or y or ""),
    }


def _infer_chart_type(columns: list[str], records: list[dict[str, Any]]) -> str:
    if not records or len(columns) < 2:
        return "table"
    if len(records) > 30:
        return "line"
    return "bar"


def _guess_x(columns: list[str], records: list[dict[str, Any]]) -> str | None:
    for col in columns:
        value = next((r[col] for r in records if r.get(col) is not None), None)
        if isinstance(value, str):
            return col
    return columns[0] if columns else None


def _guess_y(columns: list[str], records: list[dict[str, Any]], x: str | None) -> str | None:
    for col in columns:
        if col == x:
            continue
        value = next((r[col] for r in records if r.get(col) is not None), None)
        if isinstance(value, (int, float)):
            return col
    return None


def render_figure_json(
    config: dict[str, Any],
    columns: list[str],
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """根据配置真正渲染图表，返回 Plotly figure 的 JSON dict。"""
    cfg = normalize_chart_config(config, columns, records)
    chart_type = cfg["type"]
    if chart_type in {"none", "table"} or not records:
        return None

    x_key, y_key = cfg.get("x"), cfg.get("y")
    if not x_key:
        return None

    x_values = [r.get(x_key) for r in records]
    y_values = [r.get(y_key) for r in records] if y_key else None
    title = cfg["title"]

    if chart_type == "bar":
        fig = go.Figure(go.Bar(x=x_values, y=y_values, marker_color="#4C78A8"))
    elif chart_type == "line":
        fig = go.Figure(
            go.Scatter(x=x_values, y=y_values, mode="lines+markers", line=dict(color="#4C78A8", width=2.5))
        )
    elif chart_type == "area":
        fig = go.Figure(
            go.Scatter(x=x_values, y=y_values, mode="lines", fill="tozeroy",
                       line=dict(color="#4C78A8"))
        )
    elif chart_type == "pie":
        fig = go.Figure(go.Pie(labels=x_values, values=y_values, hole=0.35))
    elif chart_type == "scatter":
        fig = go.Figure(go.Scatter(x=x_values, y=y_values, mode="markers"))
    elif chart_type == "histogram":
        fig = go.Figure(go.Histogram(x=x_values if not y_values else y_values))
    else:
        return None

    fig.update_layout(
        title=title,
        xaxis_title=cfg.get("x_label") or "",
        yaxis_title=cfg.get("y_label") or "",
        template="plotly_white",
        margin=dict(l=50, r=30, t=60, b=50),
        height=420,
    )
    if chart_type != "pie" and y_key:
        # 数值轴留出空间，避免标签被裁掉
        fig.update_yaxes(automargin=True)
    return json.loads(fig.to_json())


def figure_html(fig_json: dict[str, Any]) -> str:
    """把 figure JSON 转成可内嵌的 HTML 片段（用于 Markdown 报告）。"""
    fig = go.Figure(fig_json)
    return fig.to_html(include_plotlyjs="cdn", full_html=False)
