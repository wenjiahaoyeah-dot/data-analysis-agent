"""报告接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, get_repo
from app.repository.models import User
from app.schemas.response import ReportResponse
from app.services.report_service import get_or_create_report

router = APIRouter(prefix="/api", tags=["report"])


def _load_task(task_id: str, user: User) -> dict:
    task = get_repo().get_task(task_id)
    if not task or task.get("owner_id") != user.id:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return task


@router.get("/report/{task_id}", response_model=ReportResponse, summary="按需生成或获取 Markdown 分析报告")
async def get_report(task_id: str, user: User = Depends(get_current_user)):
    task = await run_in_threadpool(_load_task, task_id, user)
    markdown = await run_in_threadpool(get_or_create_report, task)

    return ReportResponse(
        task_id=task_id,
        markdown=markdown,
        download_url=f"/api/report/{task_id}/download",
    )


@router.get("/report/{task_id}/download", summary="下载 Markdown 报告文件")
async def download_report(task_id: str, user: User = Depends(get_current_user)):
    task = await run_in_threadpool(_load_task, task_id, user)
    markdown = await run_in_threadpool(get_or_create_report, task)
    # 直接返回内容而不是 FileResponse：PG 后端下报告存在数据库里，没有磁盘路径
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="report_{task_id}.md"'},
    )
