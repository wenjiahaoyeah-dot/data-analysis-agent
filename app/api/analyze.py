"""分析任务接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, get_repo
from app.config import settings
from app.repository.models import User
from app.repository.base import RepositoryConflict
from app.schemas.response import AnalyzeRequest, AnalyzeResponse, TaskStatusResponse
from app.services.analyze_service import ConversationNotFound, run_analysis

router = APIRouter(prefix="/api", tags=["analysis"])


@router.get("/conversations", summary="列出当前用户和数据集的会话")
async def list_conversations(dataset_id: str = Query(pattern=r"^[0-9a-f]{12}$"),
                             user: User = Depends(get_current_user)):
    repo = get_repo()
    dataset = await run_in_threadpool(repo.get_dataset, dataset_id)
    if not dataset or dataset.owner_id != user.id:
        raise HTTPException(status_code=404, detail="数据集不存在")
    items = await run_in_threadpool(repo.list_conversations, user.id, dataset_id, 20)
    return {"items": [{"session_id": c["session_id"], "dataset_id": c["dataset_id"],
                       "updated_at": c.get("updated_at", ""), "last_question": c.get("last_question", "")}
                      for c in items]}


@router.get("/conversations/{session_id}", summary="恢复当前用户的会话")
async def get_conversation(session_id: str = Path(pattern=r"^[0-9a-f]{32}$"),
                           user: User = Depends(get_current_user)):
    repo = get_repo()
    conversation = await run_in_threadpool(repo.get_conversation, session_id)
    if not conversation or conversation.get("owner_id") != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    dataset = await run_in_threadpool(repo.get_dataset, conversation["dataset_id"])
    if not dataset or dataset.owner_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    items = []
    for task_id in conversation.get("task_ids") or []:
        task = await run_in_threadpool(repo.get_task, task_id)
        if task and task.get("owner_id") == user.id and task.get("session_id") == session_id:
            items.append({"question": task["question"], "result": AnalyzeResponse(**task).model_dump()})
    return {"session_id": session_id, "dataset_id": conversation["dataset_id"], "items": items}


@router.delete("/conversations/{session_id}", summary="删除会话记忆")
async def delete_conversation(session_id: str = Path(pattern=r"^[0-9a-f]{32}$"),
                              user: User = Depends(get_current_user)):
    deleted = await run_in_threadpool(get_repo().delete_conversation, session_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"deleted": True}


@router.post("/analyze", response_model=AnalyzeResponse, summary="提交自然语言分析任务（同步返回完整结果）")
async def analyze(req: AnalyzeRequest, user: User = Depends(get_current_user)):
    meta = await run_in_threadpool(get_repo().get_dataset, req.dataset_id)
    if meta is None or meta.owner_id != user.id:
        raise HTTPException(status_code=404, detail=f"数据集 {req.dataset_id} 不存在，请先上传")

    # 没有可用模型时提前拦截，不向用户返回伪造的分析结果。
    if settings.use_mock_llm:
        raise HTTPException(
            status_code=400,
            detail="数据分析需要支持 Function Calling 的真实模型；请配置 LLM_API_KEY 和模型服务",
        )

    try:
        task = await run_in_threadpool(run_analysis, req.dataset_id, req.question, None, user.id,
                                       req.mode, req.session_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会话不存在或与当前数据集不匹配") from exc
    except RepositoryConflict as exc:
        raise HTTPException(status_code=409, detail="会话已被其他请求更新，请重试") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"数据集 {req.dataset_id} 不存在，请先上传") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"分析任务执行异常：{exc}") from exc
    return AnalyzeResponse(**task)


@router.get("/task/{task_id}", response_model=TaskStatusResponse, summary="查询任务执行状态")
async def get_task(task_id: str, user: User = Depends(get_current_user)):
    task = await run_in_threadpool(get_repo().get_task, task_id)
    if not task or task.get("owner_id") != user.id:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return TaskStatusResponse(
        task_id=task["task_id"],
        status=task["status"],
        question=task["question"],
        dataset_id=task["dataset_id"],
        created_at=task["created_at"],
        elapsed_ms=task.get("elapsed_ms", 0.0),
        row_count=task.get("row_count", 0),
        error=task.get("error"),
    )


@router.get("/tasks", summary="列出当前租户最近的分析任务")
async def list_tasks(limit: int = 20, user: User = Depends(get_current_user)):
    items = await run_in_threadpool(get_repo().list_tasks, limit, user.id)
    return {
        "items": [
            {
                "task_id": t["task_id"],
                "question": t["question"],
                "status": t["status"],
                "dataset_id": t["dataset_id"],
                "created_at": t["created_at"],
                "elapsed_ms": t.get("elapsed_ms", 0.0),
            }
            for t in items
        ]
    }
