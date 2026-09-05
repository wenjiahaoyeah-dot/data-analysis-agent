"""任务存储：全部委托给控制面 Repository。

这个模块现在只是一个薄壳（保留原函数名，避免上层 import 改动）。
真正的实现（JSON 文件 / PostgreSQL）由 app/repository 决定。
"""
from __future__ import annotations

from typing import Any

from app.repository.factory import get_repository


def save_task(task: dict[str, Any]) -> None:
    get_repository().save_task(task)


def get_task(task_id: str) -> dict[str, Any] | None:
    return get_repository().get_task(task_id)


def list_tasks(
    limit: int = 20,
    owner_id: str | None = None,
    dataset_id: str | None = None,
) -> list[dict[str, Any]]:
    return get_repository().list_tasks(limit=limit, owner_id=owner_id, dataset_id=dataset_id)
