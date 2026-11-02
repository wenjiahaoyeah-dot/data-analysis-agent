"""分析服务：串起「取数据集 -> 跑 Agent -> 落任务」。

API 层只负责协议转换，业务编排都在这里，方便脚本、测试、前端复用同一套逻辑。
"""
from __future__ import annotations

import uuid
import json
from datetime import datetime
from typing import Any

from app.agent.checkpoints import CheckpointUnavailable, task_lock
from app.agent.tool_agent import run_tool_agent
from app.agent.llm import get_llm
from app.repository.models import ANONYMOUS_USER
from app.repository.base import RepositoryConflict
from app.repository.factory import get_repository
from app.services import task_store
from app.services.upload_service import get_db, load_meta
from app.tools.chart_tool import rows_to_records


class ConversationNotFound(LookupError):
    pass


def _history_turns(turns: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    """整轮淘汰，避免截断 assistant/tool 对而破坏模型协议。"""
    kept = turns[-8:]
    while len(kept) > 1 and len(json.dumps(kept, ensure_ascii=False, default=str)) > 60000:
        kept = kept[1:]
    return kept


def run_analysis(
    dataset_id: str,
    question: str,
    task_id: str | None = None,
    owner_id: str = ANONYMOUS_USER,
    mode: str = "agent",
    session_id: str | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """执行一次自然语言分析，返回可直接序列化的任务字典。"""
    task_id = task_id or uuid.uuid4().hex[:12]
    with task_lock(task_id):
        return _run_analysis(dataset_id, question, task_id, owner_id, mode, session_id, resume)


def _run_analysis(dataset_id: str, question: str, task_id: str, owner_id: str,
                  mode: str, session_id: str | None, resume: bool) -> dict[str, Any]:
    if mode != "agent":
        raise ValueError("仅支持自主工具 Agent")
    meta = load_meta(dataset_id)  # 不存在时抛 FileNotFoundError
    db = get_db(dataset_id)

    created_at = datetime.now().isoformat(timespec="seconds")

    repo = get_repository()
    previous_task = repo.get_task(task_id) if resume else None
    if resume:
        if not previous_task or previous_task.get("owner_id") != owner_id or previous_task.get("dataset_id") != dataset_id:
            raise ConversationNotFound("任务不存在")
        if previous_task.get("status") != "running" and previous_task.get("error_type") != "graph_error":
            raise CheckpointUnavailable("任务已结束，不能恢复")
        question = previous_task["question"]
        session_id = previous_task.get("session_id")
        created_at = previous_task["created_at"]
        if not session_id or previous_task.get("checkpoint_conversation_version") is None:
            raise CheckpointUnavailable("该任务没有可恢复的会话记录")
    if session_id:
        conversation = repo.get_conversation(session_id)
        if not conversation or conversation.get("owner_id") != owner_id or conversation.get("dataset_id") != dataset_id:
            raise ConversationNotFound("会话不存在或与当前数据集不匹配")
    else:
        session_id = uuid.uuid4().hex
        conversation = {"session_id": session_id, "owner_id": owner_id, "dataset_id": dataset_id,
                        "turns": [], "task_ids": [], "version": 0, "created_at": created_at}
        # 先保存会话身份，进程异常退出后才能授权恢复尚未完成的首轮任务。
        repo.save_conversation(conversation, expected_version=0)
        conversation["version"] = 1
    if resume and conversation.get("version", 0) != previous_task.get("checkpoint_conversation_version"):
        raise RepositoryConflict("会话在任务中断后已更新，不能恢复旧状态")
    turns = _history_turns(conversation.get("turns") or [])
    history = [message for turn in turns for message in turn]
    if not resume:
        # 图执行前登记任务，服务重启后可通过任务列表找到未完成工作。
        task_store.save_task({"task_id": task_id, "dataset_id": dataset_id, "owner_id": owner_id,
            "question": question, "status": "running", "mode": mode, "session_id": session_id,
            "created_at": created_at, "checkpoint_conversation_version": conversation.get("version", 0)})
    kwargs = {"history": history, "repo": repo}
    if resume:
        kwargs["resume"] = True
    final = run_tool_agent(db, meta, question, task_id, owner_id, **kwargs)
    new_turn = final["messages"][len(history):]
    conversation["turns"] = _history_turns([*turns, new_turn])
    conversation["task_ids"] = [*(conversation.get("task_ids") or []), task_id][-20:]
    conversation["updated_at"] = datetime.now().isoformat(timespec="microseconds")
    conversation["last_question"] = question[:100]
    # 任务先落库，避免会话冲突时完全丢失已产生的结果。
    pending_conversation = conversation

    query_result = final.get("query_result") or {}
    columns = query_result.get("columns", [])
    rows = query_result.get("rows", [])
    records = rows_to_records(columns, rows)

    trace = [
        {
            "node": step.get("node"),
            "status": step.get("status"),
            "detail": step.get("detail", ""),
            "elapsed_ms": step.get("elapsed_ms", 0),
            "engine": step.get("engine"),
            "query_elapsed_ms": step.get("query_elapsed_ms"),
        }
        for step in final.get("trace", [])
    ]
    repairs = [
        {
            "attempt": rec.get("attempt"),
            "failed_sql": rec.get("failed_sql"),
            "error": rec.get("error"),
            "error_type": rec.get("error_type"),
            "fixed_sql": rec.get("fixed_sql"),
            "ok": rec.get("ok", False),
            "note": rec.get("note"),
        }
        for rec in final.get("repair_history", [])
    ]

    status = final.get("status", "failed")
    task: dict[str, Any] = {
        "task_id": task_id,
        "dataset_id": dataset_id,
        "owner_id": owner_id,
        "question": question,
        "status": status if status in {"success", "needs_clarification"} else "failed",
        "mode": mode,
        "session_id": session_id,
        "clarification": final.get("clarification"),
        "tool_calls": final.get("calls", []),
        "queries": final.get("queries", []),
        "selected_result_id": final.get("selected_result_id"),
        "usage": final.get("usage", {}),
        "sql": final.get("sql"),
        "sql_explanation": final.get("sql_explanation"),
        "columns": columns,
        "data": records,
        "row_count": query_result.get("row_count", 0),
        "truncated": bool(query_result.get("truncated")),
        "analysis": final.get("analysis"),
        # 自主 Agent 只给一段自然语言答复，不产出结构化 insights；
        # 这里派生一份，避免前端"关键指标/洞察"区块在 agent 模式下空着
        "insights": final.get("insights") or _derive_insights(final.get("analysis")),
        "key_metrics": final.get("key_metrics") or _derive_metrics(records),
        "chart": final.get("chart_config") or {},
        "chart_result_id": final.get("chart_result_id"),
        "charts": final.get("charts", []),
        "chart_figure": final.get("chart_figure"),
        "plan": final.get("plan"),
        "metric_check": final.get("metric_check") or {},
        "trace": trace,
        "repair_history": repairs,
        "retry_count": final.get("retry_count", 0),
        "error": final.get("error") or None,
        "error_type": final.get("error_type") or None,
        "elapsed_ms": final.get("elapsed_ms", 0.0),
        "llm_mode": final.get("llm_mode") or get_llm().name,
        "model_name": final.get("model_name") or getattr(get_llm(), "model", get_llm().name),
        "created_at": created_at,
        "checkpoint_conversation_version": pending_conversation.get("version", 0),
        "dataset_files": meta.get("files", []),
    }

    task_store.save_task(task)

    if pending_conversation is not None and final.get("error_type") != "graph_error":
        # 任务已落库，再更新会话；冲突要如实返回 409 让调用方重试
        repo.save_conversation(
            pending_conversation,
            expected_version=pending_conversation.get("version", 0),
        )
    return task


def get_task_or_none(task_id: str) -> dict[str, Any] | None:
    return task_store.get_task(task_id)


def _derive_insights(answer: str | None) -> list[str]:
    """把 Agent 的一段答复切成要点，供前端洞察区块展示。"""
    if not answer:
        return []
    import re

    parts = [p.strip() for p in re.split(r"[。！？\n]+", answer) if p.strip()]
    return [p for p in parts if len(p) >= 8][:3]


def _derive_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """从结果首行提取数值字段，作为"核心指标"的兜底。"""
    if not records:
        return {}
    first = records[0]
    out: dict[str, Any] = {}
    for key, value in first.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[str(key)] = value
        if len(out) >= 4:
            break
    return out
