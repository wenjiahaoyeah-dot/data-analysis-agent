"""查询审计接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, get_repo
from app.repository.models import User

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/logs", summary="列出当前租户的查询审计记录")
async def list_logs(
    limit: int = 50,
    dataset_id: str | None = None,
    user: User = Depends(get_current_user),
):
    records = await run_in_threadpool(get_repo().list_audits, user.id, dataset_id, limit)
    return {"items": [r.to_dict() for r in records]}


@router.get("/stats", summary="查询统计（成功率、P95、慢查询、错误分布）")
async def stats(user: User = Depends(get_current_user)):
    return await run_in_threadpool(get_repo().audit_stats, user.id)
