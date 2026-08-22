"""数据接入接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, get_repo
from app.config import settings
from app.repository.models import User
from app.schemas.response import SchemaResponse, UploadResponse
from app.services.upload_service import (
    UploadError,
    create_dataset,
    list_datasets,
)

router = APIRouter(prefix="/api", tags=["dataset"])


@router.post("/upload", response_model=UploadResponse, summary="上传 CSV / Excel 并注册到 DuckDB")
async def upload_files(
    files: list[UploadFile] = File(..., description="支持多文件，每个文件建成一张表"),
    user: User = Depends(get_current_user),
):
    payload: list[tuple[str, bytes]] = []
    max_bytes = settings.max_upload_mb * 1024 * 1024
    total_bytes = 0
    for file in files:
        if not file.filename:
            raise HTTPException(status_code=400, detail="文件名为空")
        # 限制单文件和请求总量，并在读入内存之前拒绝超限数据。
        content = await file.read(max_bytes - total_bytes + 1)
        total_bytes += len(content)
        if total_bytes > max_bytes:
            raise HTTPException(status_code=413, detail=f"上传文件总大小超过 {settings.max_upload_mb}MB 限制")
        if not content:
            raise HTTPException(status_code=400, detail=f"文件 {file.filename} 内容为空")
        payload.append((file.filename, content))

    try:
        meta = await run_in_threadpool(create_dataset, payload, user.id)
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # 兜底，避免 500 直接抛栈给前端
        raise HTTPException(status_code=500, detail=f"数据接入失败：{exc}") from exc

    return UploadResponse(
        dataset_id=meta.dataset_id,
        tables=meta.tables,
        files=meta.files,
        total_rows=meta.total_rows,
        profiles=meta.profiles,
        warnings=meta.warnings,
        created_at=meta.created_at,
    )


@router.get("/schema/{dataset_id}", response_model=SchemaResponse, summary="获取数据集表结构")
async def get_schema(dataset_id: str, user: User = Depends(get_current_user)):
    meta = await run_in_threadpool(get_repo().get_dataset, dataset_id)
    if meta is None or meta.owner_id != user.id:
        # 不区分"不存在"和"不是你的"，避免探测他人数据集 id
        raise HTTPException(status_code=404, detail=f"数据集 {dataset_id} 不存在")
    return SchemaResponse(
        dataset_id=dataset_id,
        tables=meta.tables,
        created_at=meta.created_at,
    )


@router.get("/datasets", summary="列出当前租户已上传的数据集")
async def list_dataset(limit: int = 20, user: User = Depends(get_current_user)):
    items = await run_in_threadpool(list_datasets, limit, user.id)
    return {
        "items": [
            {
                "dataset_id": item["dataset_id"],
                "created_at": item["created_at"],
                "files": item.get("files", []),
                "tables": [t["table_name"] for t in item.get("tables", [])],
                "total_rows": sum(t["row_count"] for t in item.get("tables", [])),
            }
            for item in items
        ]
    }
