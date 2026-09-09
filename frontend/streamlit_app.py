"""Streamlit 前端：聊天式数据分析界面。

启动：
    streamlit run frontend/streamlit_app.py
（需要先启动后端：uvicorn app.main:app --port 8000）
"""
from __future__ import annotations

import json
import inspect
import os
from pathlib import Path

import httpx
import pandas as pd
import plotly.io as pio
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = PROJECT_ROOT / "data"

EXAMPLES = [
    "统计最近三个月每个月的销售额",
    "哪个地区的销售额最高？",
    "找出销量排名前五的商品",
    "各渠道的订单量和客单价分别是多少",
    "分析最近三个月销售额的变化趋势，并说明可能的原因",
    "各类目的销售额占比",
]

st.set_page_config(page_title="智能数据分析 Agent", page_icon="📊", layout="wide")

# Streamlit 1.50+ 用 width="stretch"，旧版本用 use_container_width，做一层兼容
_ST_VER = tuple(int(x) for x in st.__version__.split(".")[:2])


def _full_width() -> dict:
    return {"width": "stretch"} if _ST_VER >= (1, 50) else {"use_container_width": True}


# ---------------------------------------------------------------------- #
# API 封装
# ---------------------------------------------------------------------- #
def _auth_headers() -> dict:
    """带上租户标识。留空则落到 anonymous，与后端默认行为一致。"""
    key = st.session_state.get("api_key", os.getenv("API_KEY", ""))
    return {"X-API-Key": key} if key else {}


def _clear_identity_state() -> None:
    """切换身份或后端时，清除前一身份的页面数据。"""
    st.session_state.dataset = None
    _clear_conversation()


def _clear_conversation() -> None:
    st.session_state.history = []
    st.session_state.pending_question = None
    st.session_state.session_id = None
    st.session_state.report_cache = {}
    st.session_state.stats = {"total": 0, "success": 0, "pending": 0, "repaired": 0, "elapsed": []}


def api_get(path: str, timeout: float = 30.0) -> dict:
    with httpx.Client(timeout=timeout, headers=_auth_headers()) as client:
        resp = client.get(f"{API_BASE}{path}")
        resp.raise_for_status()
        return resp.json()


def api_post(path: str, json_body: dict | None = None, files=None, timeout: float = 240.0) -> dict:
    with httpx.Client(timeout=timeout, headers=_auth_headers()) as client:
        if files is not None:
            resp = client.post(f"{API_BASE}{path}", files=files)
        else:
            resp = client.post(f"{API_BASE}{path}", json=json_body)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
        return resp.json()


def api_delete(path: str) -> dict:
    with httpx.Client(timeout=30, headers=_auth_headers()) as client:
        resp = client.delete(f"{API_BASE}{path}")
        resp.raise_for_status()
        return resp.json()


def upload_files(file_items: list[tuple[str, bytes]]) -> dict:
    files = [("files", (name, content, "application/octet-stream")) for name, content in file_items]
    return api_post("/api/upload", files=files)


# ---------------------------------------------------------------------- #
# 渲染
# ---------------------------------------------------------------------- #
def render_chart(fig_json: dict | None) -> None:
    if not fig_json:
        return
    try:
        fig = pio.from_json(json.dumps(fig_json))
        st.plotly_chart(fig, **_full_width())
    except Exception as exc:
        st.warning(f"图表渲染失败：{exc}")


def render_trace(trace: list[dict]) -> None:
    if not trace:
        return
    rows = []
    for i, step in enumerate(trace, 1):
        status_icon = {"ok": "✅", "warn": "⚠️", "error": "❌", "reject": "🛑"}.get(step.get("status"), "•")
        rows.append(
            {
                "步骤": i,
                "节点": step.get("node"),
                "状态": f"{status_icon} {step.get('status')}",
                "耗时(ms)": step.get("elapsed_ms"),
                "说明": step.get("detail", ""),
                "引擎": step.get("engine") or "-",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, **_full_width())


def render_result(result: dict, allow_clarification: bool = True) -> None:
    status = result.get("status")
    if status == "needs_clarification":
        clarification = result.get("clarification") or {}
        st.info(clarification.get("question") or "请补充分析口径。")
        st.caption("请在输入框回答，也可以选择下面的选项。")
        if allow_clarification:
            for index, option in enumerate(clarification.get("options") or []):
                if st.button(option, key=f"clarify_{result['task_id']}_{index}"):
                    st.session_state.pending_question = option
        with st.expander("查看已执行的工具"):
            render_trace(result.get("trace", []))
        return
    if status != "success":
        st.error(f"任务失败（{result.get('error_type')}）：{result.get('error')}")
        with st.expander("查看执行链路"):
            render_trace(result.get("trace", []))
        return

    cols = st.columns([3, 1])
    with cols[0]:
        st.markdown(result.get("analysis") or "（无结论）")
    with cols[1]:
        st.metric("主要查询返回行数", result.get("row_count", 0))

    check = result.get("metric_check") or {}
    check_status = check.get("status") or "not_applicable"
    if check_status != "not_applicable":
        icon = {"pass": "✅", "info": "ℹ️", "deviation": "⚠️"}.get(check_status, "•")
        label = {"pass": "口径一致", "info": "口径提示", "deviation": "口径偏离"}.get(check_status, check_status)
        if check_status == "deviation":
            st.warning(f"{icon} {label}：{check.get('message')}")
        else:
            st.info(f"{icon} {label}：{check.get('message')}")

    insights = result.get("insights") or []
    if insights:
        for item in insights:
            st.markdown(f"- {item}")

    charts = result.get("charts") or []
    if charts:
        for chart in charts:
            st.caption(f"图表使用查询结果 {chart.get('result_id')}")
            render_chart(chart.get("figure"))
    else:
        render_chart(result.get("chart_figure"))

    tab_data, tab_sql, tab_trace, tab_report = st.tabs(["📋 数据", "🧾 SQL", "🔀 Agent 链路", "📄 报告"])
    with tab_data:
        selected_id = result.get("selected_result_id")
        if selected_id:
            st.caption(f"主要查询结果：{selected_id}")
        if result.get("data"):
            st.dataframe(
                pd.DataFrame(result["data"], columns=result.get("columns") or None),
                hide_index=True,
                **_full_width(),
            )
            if result.get("truncated"):
                st.caption("结果已按行数上限截断")
        else:
            st.info("本次没有返回数据行。")
        queries = result.get("queries") or []
        if len(queries) > 1:
            st.markdown("**本轮其他查询结果**")
            primary_id = selected_id or queries[-1].get("result_id")
            for query in queries:
                if query.get("result_id") == primary_id:
                    continue
                with st.expander(f"{query.get('result_id')} · {query.get('row_count', 0)} 行"):
                    st.code(query.get("sql", ""), language="sql")
                    st.dataframe(pd.DataFrame(query.get("data") or [], columns=query.get("columns") or None),
                                 hide_index=True, **_full_width())
                    if query.get("truncated"):
                        st.caption("结果已按行数上限截断")
    with tab_sql:
        st.code(result.get("sql") or "", language="sql")
        if result.get("sql_explanation"):
            st.caption(result["sql_explanation"])
        repairs = result.get("repair_history") or []
        if repairs:
            st.markdown(f"**SQL 失败与修复记录：{len(repairs)} 条**")
            for rec in repairs:
                with st.expander(f"第 {rec['attempt']} 次：{rec.get('error_type')}"):
                    st.markdown("**失败 SQL**")
                    st.code(rec.get("failed_sql") or "", language="sql")
                    st.markdown(f"**数据库报错**：`{rec.get('error')}`")
                    st.markdown(f"**修复说明**：{rec.get('note')}")
                    if rec.get("fixed_sql"):
                        st.markdown("**修复后 SQL**")
                        st.code(rec["fixed_sql"], language="sql")
    with tab_trace:
        render_trace(result.get("trace", []))
        meta = st.columns(3)
        meta[0].caption(f"模型：{result.get('model_name') or result.get('llm_mode')}")
        meta[1].caption(f"总耗时：{result.get('elapsed_ms')} ms")
        meta[2].caption(f"重试次数：{result.get('retry_count')}")
        if result.get("mode") == "agent":
            st.caption(f"工具调用 {len(result.get('tool_calls') or [])} 次 · 模型 token 用量：{result.get('usage') or '服务商未返回'}")
            for call in result.get("tool_calls") or []:
                with st.expander(f"{call.get('name')} · {call.get('status')}"):
                    st.json(call.get("arguments") or {})
    with tab_report:
        task_id = result["task_id"]
        reports = st.session_state.setdefault("report_cache", {})
        st.caption("需要时点击生成，报告使用已有分析结果，不会再次调用大模型。")
        if st.button("生成报告", key=f"generate_report_{task_id}"):
            try:
                with st.spinner("正在生成报告…"):
                    reports[task_id] = api_get(f"/api/report/{task_id}")["markdown"]
            except Exception as exc:
                st.warning(f"报告生成失败：{exc}")
        if task_id in reports:
            st.markdown(reports[task_id])
            st.download_button(
                "下载 Markdown 报告",
                data=reports[task_id].encode("utf-8"),
                file_name=f"report_{task_id}.md",
                mime="text/markdown",
                key=f"download_report_{task_id}",
            )


def render_history() -> None:
    for index, item in enumerate(st.session_state.history):
        with st.chat_message("user"):
            st.markdown(item["question"])
        with st.chat_message("assistant"):
            render_result(item["result"], allow_clarification=index == len(st.session_state.history) - 1)


# ---------------------------------------------------------------------- #
# 侧边栏
# ---------------------------------------------------------------------- #
def sidebar() -> dict | None:
    st.sidebar.title("📊 数据分析 Agent")
    st.sidebar.caption("LangGraph · Text-to-SQL · DuckDB · SQLGlot")

    global API_BASE
    API_BASE = st.sidebar.text_input("后端地址", value=API_BASE, on_change=_clear_identity_state)

    # 轻量身份：不同 Key 看到的数据集与任务互相隔离
    st.session_state.setdefault("api_key", os.getenv("API_KEY", ""))
    st.sidebar.text_input(
        "API Key（可选，用于租户隔离）",
        key="api_key",
        type="password",
        on_change=_clear_identity_state,
        help="认证模式下请输入已授权的 Key。默认演示模式不验证 Key。",
    )

    try:
        health = api_get("/api/health", timeout=5)
        if health["llm_mode"] != "real":
            st.sidebar.warning("后端在线 · 请配置支持工具调用的模型")
        else:
            st.sidebar.success(f"后端在线 · {health['llm_model']}")
    except Exception:
        st.sidebar.error("后端未连接，请先启动：\n`uvicorn app.main:app --port 8000`")
        return None

    st.sidebar.divider()
    st.sidebar.subheader("1. 上传数据")

    demo_cols = st.sidebar.columns(2)
    if demo_cols[0].button("加载演示数据", **_full_width()):
        paths = [DEMO_DIR / "demo_sales.csv", DEMO_DIR / "demo_products.csv"]
        items = [(p.name, p.read_bytes()) for p in paths if p.exists()]
        if not items:
            st.sidebar.error("找不到演示数据，请先运行 scripts/make_demo_data.py")
        else:
            try:
                with st.spinner("正在解析并注册到 DuckDB..."):
                    data = upload_files(items)
                _clear_conversation()
                st.session_state.dataset = data
                st.sidebar.success(f"已加载 {data['total_rows']} 行数据")
            except Exception as exc:
                st.sidebar.error(f"上传失败：{exc}")

    if demo_cols[1].button("清空会话", **_full_width()):
        _clear_conversation()
        st.rerun()

    max_upload_mb = int(health.get("max_upload_mb", 50))
    upload_options = {}
    if "max_upload_size" in inspect.signature(st.file_uploader).parameters:
        upload_options["max_upload_size"] = max_upload_mb
    uploaded = st.sidebar.file_uploader(
        "或上传 CSV / Excel", type=["csv", "xlsx", "xls"], accept_multiple_files=True,
        **upload_options,
    )
    st.sidebar.caption(f"每次上传合计不超过 {max_upload_mb} MB")
    if uploaded and st.sidebar.button("上传并建表", **_full_width()):
        try:
            with st.spinner("正在解析并注册到 DuckDB..."):
                data = upload_files([(f.name, f.getvalue()) for f in uploaded])
            _clear_conversation()
            st.session_state.dataset = data
            st.sidebar.success(f"已加载 {data['total_rows']} 行数据")
        except Exception as exc:
            st.sidebar.error(f"上传失败：{exc}")

    dataset = st.session_state.get("dataset")
    if not dataset:
        try:
            datasets = api_get("/api/datasets").get("items", [])
            if datasets:
                options = {f"{d['dataset_id']} · {', '.join(d['tables'])}": d["dataset_id"] for d in datasets}
                picked = st.sidebar.selectbox("选择已有数据集", list(options.keys()), index=None,
                                              placeholder="请选择数据集")
                if picked and st.sidebar.button("使用所选数据集"):
                    dataset = {"dataset_id": options[picked], "tables": [], "total_rows": 0}
                    st.session_state.dataset = dataset
                    st.rerun()
        except Exception:
            pass

    if dataset:
        st.sidebar.caption(f"当前数据集：`{dataset['dataset_id']}`")
        if st.sidebar.button("切换数据集"):
            st.session_state.dataset = None
            _clear_conversation()
            st.rerun()
        with st.sidebar.expander("查看表结构"):
            try:
                schema = api_get(f"/api/schema/{dataset['dataset_id']}")
                for table in schema["tables"]:
                    st.markdown(f"**{table['table_name']}**（{table['row_count']} 行）")
                    st.dataframe(
                        pd.DataFrame(table["columns"]),
                        hide_index=True,
                        **_full_width(),
                    )
            except Exception as exc:
                st.warning(f"结构获取失败：{exc}")

        with st.sidebar.expander("恢复或删除会话记忆"):
            try:
                sessions = api_get(f"/api/conversations?dataset_id={dataset['dataset_id']}").get("items", [])
                if sessions:
                    choices = {f"{s.get('updated_at', '')[:16]} · {s.get('last_question', '')[:25]} · {s['session_id'][:6]}":
                               s["session_id"] for s in sessions}
                    selected = st.selectbox("历史会话", list(choices))
                    restore_col, delete_col = st.columns(2)
                    if restore_col.button("恢复"):
                        loaded = api_get(f"/api/conversations/{choices[selected]}")
                        _clear_conversation()
                        st.session_state.session_id = loaded["session_id"]
                        st.session_state.history = loaded["items"]
                        stats = st.session_state.stats
                        for item in loaded["items"]:
                            previous = item["result"]
                            stats["total"] += 1
                            stats["success"] += int(previous.get("status") == "success")
                            stats["pending"] += int(previous.get("status") == "needs_clarification")
                            stats["repaired"] += int(bool(previous.get("retry_count")))
                            stats["elapsed"].append(previous.get("elapsed_ms", 0))
                        st.rerun()
                    if delete_col.button("删除记忆"):
                        session_to_delete = choices[selected]
                        api_delete(f"/api/conversations/{session_to_delete}")
                        if st.session_state.session_id == session_to_delete:
                            _clear_conversation()
                        st.rerun()
                    st.caption("删除会话记忆不会删除已保存的任务、报告或审计记录。")
                else:
                    st.caption("当前数据集还没有历史会话。")
            except Exception as exc:
                st.caption(f"会话读取失败：{exc}")
    st.sidebar.divider()
    st.sidebar.subheader("2. 试试这些问题")
    for i, example in enumerate(EXAMPLES):
        if st.sidebar.button(example, key=f"ex_{i}", **_full_width()):
            st.session_state.pending_question = example

    st.sidebar.divider()
    with st.sidebar.expander("📈 查询审计"):
        try:
            stats = api_get("/api/audit/stats", timeout=10)
            st.metric("查询总数", stats.get("total", 0))
            st.metric("成功率", f"{float(stats.get('success_rate') or 0):.0%}")
            st.metric("P95 耗时", f"{float(stats.get('p95_elapsed_ms') or 0):.0f} ms")
            logs = api_get("/api/audit/logs?limit=5", timeout=10).get("items", [])
            if logs:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "状态": row.get("status"),
                                "口径": (row.get("metric_check") or {}).get("status", "-"),
                                "耗时(ms)": row.get("elapsed_ms"),
                            }
                            for row in logs
                        ]
                    ),
                    hide_index=True,
                    **_full_width(),
                )
        except Exception as exc:
            st.caption(f"审计不可用：{exc}")

    return dataset


# ---------------------------------------------------------------------- #
# 主流程
# ---------------------------------------------------------------------- #
def main() -> None:
    st.session_state.setdefault("history", [])
    st.session_state.setdefault("dataset", None)
    st.session_state.setdefault("pending_question", None)
    st.session_state.setdefault("session_id", None)
    st.session_state.setdefault("stats", {"total": 0, "success": 0, "pending": 0, "repaired": 0, "elapsed": []})

    dataset = sidebar()

    st.title("智能数据分析 Agent")
    st.caption("用自然语言提问，Agent 自动查询和分析数据；需要时可手动生成报告。")

    if not dataset:
        st.info("👈 先在左侧上传数据（或点「加载演示数据」），然后就可以提问了。")
        return

    render_history()

    question = st.chat_input("例如：统计最近三个月每个月的销售额")
    if st.session_state.pending_question:
        question = st.session_state.pending_question
        st.session_state.pending_question = None

    if not question:
        return

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Agent 正在分析…"):
            try:
                result = api_post(
                    "/api/analyze",
                    {"dataset_id": dataset["dataset_id"], "question": question,
                     "mode": "agent",
                     "session_id": st.session_state.session_id},
                )
            except Exception as exc:
                st.error(f"调用失败：{exc}")
                return
        if result.get("session_id"):
            st.session_state.session_id = result["session_id"]
        render_result(result)

    st.session_state.history.append({"question": question, "result": result})

    stats = st.session_state.stats
    stats["total"] += 1
    if result.get("status") == "success":
        stats["success"] += 1
    if result.get("status") == "needs_clarification":
        stats["pending"] += 1
    if result.get("retry_count"):
        stats["repaired"] += 1
    stats["elapsed"].append(result.get("elapsed_ms", 0))

    with st.sidebar:
        st.divider()
        st.subheader("本次会话统计")
        total = max(stats["total"] - stats["pending"], 1)
        st.metric("提问数", stats["total"])
        st.metric("成功率", f"{stats['success'] / total:.0%}")
        if stats["pending"]:
            st.caption(f"澄清提问：{stats['pending']} 次（不计入成功率）")
        st.metric("触发过自动修复", stats["repaired"])
        if stats["elapsed"]:
            st.metric("平均耗时", f"{sum(stats['elapsed']) / len(stats['elapsed']):.0f} ms")


if __name__ == "__main__":
    main()
