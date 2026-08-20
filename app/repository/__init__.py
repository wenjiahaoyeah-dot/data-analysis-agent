"""控制面（元数据 / 任务 / 报告 / 指标 / 审计）。

与 app/database（数据面，DuckDB 分析引擎）区分：
    app/database  -> 分析执行
    app/repository -> 业务状态存储
"""
from app.repository.base import (
    GLOBAL_OWNER,
    MetadataRepository,
    RepositoryConflict,
    RepositoryError,
    RepositoryUnavailable,
)
from app.repository.factory import get_repository, reset_repository
from app.repository.models import (
    ANONYMOUS_USER,
    AuditRecord,
    DatasetMeta,
    Metric,
    QAExample,
    User,
)

__all__ = [
    "GLOBAL_OWNER",
    "MetadataRepository",
    "RepositoryConflict",
    "RepositoryError",
    "RepositoryUnavailable",
    "get_repository",
    "reset_repository",
    "ANONYMOUS_USER",
    "AuditRecord",
    "DatasetMeta",
    "Metric",
    "QAExample",
    "User",
]
