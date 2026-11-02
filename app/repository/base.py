"""控制面抽象接口（Repository Protocol）。

存在的意义不是"为了抽象而抽象"，而是：同一套业务代码必须能在
PostgreSQL 和 JSON 文件两种后端上跑出**等价结果**。用 Protocol 而不是 ABC，
是为了让 JSON 实现不必继承任何东西，保持实现自由。

行为契约（两个实现都必须满足，tests/test_repository_contract.py 会逐条验证）：
    1. 时间字段返回 ISO-8601 字符串，不是 datetime 对象；
    2. list_* 一律按 created_at 倒序；
    3. get_* 对不存在的 id 返回 None，不抛异常；
    4. save_* 对相同主键是幂等覆盖（upsert 语义）；
    5. 返回值必须是**深拷贝**，调用方修改它不能影响存储里的数据；
    6. owner_id 为 None 表示"不按租户过滤"（内部/测试用），非 None 时严格过滤。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.repository.models import AuditRecord, DatasetMeta, Metric, QAExample, User


class RepositoryError(RuntimeError):
    """控制面操作的统一异常基类。"""


class RepositoryUnavailable(RepositoryError):
    """后端不可用（连不上 PG、存储目录不可写等）。"""


class RepositoryConflict(RepositoryError):
    """唯一约束冲突。"""


@runtime_checkable
class MetadataRepository(Protocol):
    backend: str

    # ---- 生命周期 ----
    def cache_key(self) -> str:
        """返回可稳定用于缓存的键（图缓存、进程内 LRU 都要用）。"""
        ...

    def close(self) -> None:
        """释放连接池/文件句柄。测试里用于清理。"""
        ...

    def ping(self) -> bool:
        """后端是否可用。"""
        ...

    # ---- 用户 ----
    def ensure_user(self, user_id: str, display_name: str = "") -> User:
        """不存在则创建，存在则直接返回。"""
        ...

    def get_user(self, user_id: str) -> User | None: ...

    # ---- 数据集 ----
    def save_dataset(self, meta: DatasetMeta) -> None: ...

    def get_dataset(self, dataset_id: str) -> DatasetMeta | None: ...

    def list_datasets(
        self, owner_id: str | None = None, limit: int = 20
    ) -> list[DatasetMeta]: ...

    def delete_dataset(self, dataset_id: str) -> bool: ...

    # ---- 任务 ----
    def save_task(self, task: dict[str, Any]) -> None:
        """task 是 analyze_service 组装好的完整字典，原样落库。"""
        ...

    def get_task(self, task_id: str) -> dict[str, Any] | None: ...

    def list_tasks(
        self,
        limit: int = 20,
        owner_id: str | None = None,
        dataset_id: str | None = None,
    ) -> list[dict[str, Any]]: ...

    # 完整对话轮次，expected_version 用于防止并发覆盖。
    def get_conversation(self, session_id: str) -> dict[str, Any] | None: ...

    def save_conversation(self, conversation: dict[str, Any], expected_version: int) -> None: ...

    def list_conversations(self, owner_id: str, dataset_id: str, limit: int = 20) -> list[dict[str, Any]]: ...

    def delete_conversation(self, session_id: str, owner_id: str) -> bool: ...

    # ---- 报告 ----
    def save_report(self, task_id: str, markdown: str) -> None: ...

    def get_report(self, task_id: str) -> str | None: ...

    # ---- 指标 ----
    def list_metrics(self, owner_id: str | None = None) -> list[Metric]:
        """返回该租户可见的指标：owner_id 匹配的 + 全局的（owner_id='*'）。"""
        ...

    def upsert_metric(self, metric: Metric) -> Metric: ...

    def delete_metric(self, name: str, owner_id: str) -> bool: ...

    # ---- 审计 ----
    def append_audit(self, record: AuditRecord) -> AuditRecord: ...

    def audit_exists(self, task_id: str, owner_id: str) -> bool: ...

    def list_audits(
        self,
        owner_id: str | None = None,
        dataset_id: str | None = None,
        limit: int = 50,
    ) -> list[AuditRecord]: ...

    def audit_stats(self, owner_id: str | None = None) -> dict[str, Any]:
        """返回 {total, success, failed, avg_elapsed_ms, p95_elapsed_ms, slow_queries}"""
        ...

    # ---- few-shot 示例 ----
    def add_example(self, example: QAExample) -> QAExample: ...

    def find_examples(
        self,
        dataset_signature: str,
        metric_name: str | None = None,
        limit: int = 3,
    ) -> list[QAExample]: ...


GLOBAL_OWNER = "*"
"""全局指标的 owner_id 标记：所有租户都能看到，但不能改。"""
