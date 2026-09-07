"""报告生成服务：把一次分析任务的结果渲染成 Markdown。

Markdown 格式便于阅读、版本管理及下载。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.config import settings
from app.repository.factory import get_repository


def build_markdown(task: dict[str, Any]) -> str:
    question = task.get("question", "")
    status = task.get("status", "")
    lines: list[str] = []
    lines.append(f"# 数据分析报告：{question}")
    lines.append("")
    lines.append(f"- 任务 ID：`{task.get('task_id', '')}`")
    lines.append(f"- 数据集：`{task.get('dataset_id', '')}`")
    lines.append(f"- 生成时间：{task.get('created_at', datetime.now().isoformat(timespec='seconds'))}")
    lines.append(f"- 执行状态：**{status}**")
    lines.append(f"- 总耗时：{task.get('elapsed_ms', 0)} ms")
    lines.append(f"- 推理引擎：{task.get('llm_mode', 'unknown')}")
    if task.get("model_name"):
        lines.append(f"- 模型：{task['model_name']}")
    if task.get("retry_count"):
        lines.append(f"- SQL 自动修复次数：{task['retry_count']}")
    lines.append("")

    if task.get("analysis"):
        lines.append("## 一、分析结论")
        lines.append("")
        lines.append(task["analysis"])
        lines.append("")

    insights = task.get("insights") or []
    if insights:
        lines.append("## 二、关键发现")
        lines.append("")
        for idx, item in enumerate(insights, 1):
            lines.append(f"{idx}. {item}")
        lines.append("")

    metrics = task.get("key_metrics") or {}
    if metrics:
        lines.append("## 三、核心指标")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("| --- | --- |")
        for key, value in metrics.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")

    sql = task.get("sql")
    if sql:
        lines.append("## 四、执行的 SQL")
        lines.append("")
        lines.append("```sql")
        lines.append(sql)
        lines.append("```")
        if task.get("sql_explanation"):
            lines.append("")
            lines.append(f"> {task['sql_explanation']}")
        lines.append("")

    columns = task.get("columns") or []
    data = task.get("data") or []
    if columns:
        lines.append("## 五、查询结果")
        lines.append("")
        lines.append(f"共返回 {task.get('row_count', len(data))} 行"
                     + ("（已截断）" if task.get("truncated") else "") + "，预览前 20 行：")
        lines.append("")
        lines.append("| " + " | ".join(str(c) for c in columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
        for row in data[:20]:
            lines.append("| " + " | ".join(_cell(row.get(c)) for c in columns) + " |")
        lines.append("")

    queries = task.get("queries") or []
    if len(queries) > 1:
        lines.extend(["## 本轮各次查询", ""])
        for query in queries:
            lines.extend([f"### {query.get('result_id', '查询')} · {query.get('row_count', 0)} 行", "",
                          "```sql", str(query.get("sql") or ""), "```", ""])
            query_columns = query.get("columns") or []
            if query_columns:
                lines.append("| " + " | ".join(_cell(c) for c in query_columns) + " |")
                lines.append("| " + " | ".join("---" for _ in query_columns) + " |")
                for row in (query.get("data") or [])[:10]:
                    lines.append("| " + " | ".join(_cell(row.get(c)) for c in query_columns) + " |")
                lines.append("")
        if task.get("charts"):
            lines.append("图表对应查询：" + "、".join(str(c.get("result_id")) for c in task["charts"]))
            lines.append("")

    if status == "needs_clarification":
        lines.extend(["## 等待用户澄清", "", (task.get("clarification") or {}).get("question", ""), ""])

    repairs = task.get("repair_history") or []
    if repairs:
        lines.append("## 六、SQL 自动修复记录")
        lines.append("")
        for rec in repairs:
            lines.append(f"### 第 {rec.get('attempt')} 次修复")
            lines.append("")
            lines.append(f"- 错误类型：`{rec.get('error_type')}`")
            lines.append(f"- 错误信息：{rec.get('error')}")
            lines.append(f"- 修复说明：{rec.get('note')}")
            lines.append("")
            lines.append("失败 SQL：")
            lines.append("")
            lines.append("```sql")
            lines.append(str(rec.get("failed_sql") or ""))
            lines.append("```")
            lines.append("")
            if rec.get("fixed_sql"):
                lines.append("修复后 SQL：")
                lines.append("")
                lines.append("```sql")
                lines.append(str(rec["fixed_sql"]))
                lines.append("```")
                lines.append("")

    trace = task.get("trace") or []
    if trace:
        lines.append("## 七、Agent 执行链路")
        lines.append("")
        lines.append("| 步骤 | 节点 | 状态 | 耗时(ms) | 说明 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for idx, step in enumerate(trace, 1):
            lines.append(
                f"| {idx} | {step.get('node')} | {step.get('status')} | "
                f"{step.get('elapsed_ms')} | {_cell(step.get('detail'), 80)} |"
            )
        lines.append("")

    if status == "failed" and task.get("error"):
        lines.append("## 失败原因")
        lines.append("")
        lines.append(f"`{task.get('error_type')}`：{task['error']}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_本报告由 Intelligent Data Analysis Agent 自动生成。结论仅基于所查询数据的描述性统计，不构成因果推断。_")
    return "\n".join(lines)


def get_or_create_report(task: dict[str, Any]) -> str:
    """显式请求报告时才生成并保存；已生成的报告直接复用。"""
    repo = get_repository()
    markdown = repo.get_report(task["task_id"])
    if markdown is None:
        markdown = build_markdown(task)
        repo.save_report(task["task_id"], markdown)
    return markdown


def _cell(value: Any, limit: int | None = None) -> str:
    if value is None:
        return "-"
    text = str(value).replace("|", "\\|").replace("\n", " ")
    if limit and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text
