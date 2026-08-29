"""Repository 工厂：按配置返回进程级单例。

不做自动降级（PG 连不上就报错，不会偷偷退回 JSON）——控制面数据分叉到
两个后端的排查成本，远高于直接失败。
"""
from __future__ import annotations

import threading

from app.config import settings
from app.repository.base import MetadataRepository, RepositoryUnavailable
from app.repository.jsonfile import JsonRepository

_INSTANCE: MetadataRepository | None = None
_LOCK = threading.Lock()


def _build() -> MetadataRepository:
    backend = (settings.repository_backend or "json").strip().lower()
    if backend in {"postgres", "postgresql", "pg"}:
        from app.repository.postgres import PostgresRepository

        return PostgresRepository(
            dsn=settings.postgres_dsn,
            schema=settings.postgres_schema,
            pool_size=settings.postgres_pool_size,
        )
    if backend in {"json", "jsonfile", "file"}:
        return JsonRepository()
    raise RepositoryUnavailable(f"未知的控制面后端：{backend}（可选 json / postgres）")


def get_repository(force_new: bool = False) -> MetadataRepository:
    global _INSTANCE
    if _INSTANCE is not None and not force_new:
        return _INSTANCE
    with _LOCK:
        if _INSTANCE is None or force_new:
            _INSTANCE = _build()
    return _INSTANCE


def reset_repository() -> None:
    """测试用：清空单例，让下一次 get_repository() 按新配置重建。"""
    global _INSTANCE
    with _LOCK:
        if _INSTANCE is not None:
            try:
                _INSTANCE.close()
            except Exception:  # pragma: no cover
                pass
        _INSTANCE = None
