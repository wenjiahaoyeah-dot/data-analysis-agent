"""指标语义层接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, get_repo
from app.repository.models import Metric, User
from app.services.metric_service import seed_default_metrics

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


class MetricPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=64)
    expression: str = Field(..., min_length=1, max_length=300)
    description: str = ""
    required_columns: list[str] = Field(default_factory=list)
    default_filter: str | None = None
    unit: str = ""
    synonyms: list[str] = Field(default_factory=list)


@router.get("", summary="列出当前租户可见的指标（含全局指标）")
async def list_metrics(user: User = Depends(get_current_user)):
    items = await run_in_threadpool(get_repo().list_metrics, user.id)
    return {"items": [m.to_dict() for m in items]}


@router.post("", summary="新建或更新指标（按 name 幂等）")
async def upsert_metric(payload: MetricPayload, user: User = Depends(get_current_user)):
    metric = Metric(owner_id=user.id, **payload.model_dump())
    saved = await run_in_threadpool(get_repo().upsert_metric, metric)
    return saved.to_dict()


@router.delete("/{name}", summary="删除自己的指标")
async def delete_metric(name: str, user: User = Depends(get_current_user)):
    ok = await run_in_threadpool(get_repo().delete_metric, name, user.id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"指标 {name} 不存在")
    return {"deleted": name}


@router.post("/seed", summary="预置默认指标（幂等）")
async def seed(user: User = Depends(get_current_user)):
    created = await run_in_threadpool(seed_default_metrics)
    return {"created": created}
