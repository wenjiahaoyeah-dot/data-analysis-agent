"""控制面数据模型。

设计约束（很重要）：
    所有时间字段一律用 **TEXT ISO-8601 字符串**，数值用 int / float。
    不用 datetime / Decimal，因为 PostgreSQL 的 timestamptz 会被 psycopg 解成
    datetime 对象，而 JSON 实现天然只能给字符串——两边一混，Repository 的
    「双实现可互换」就无法验证了。统一成字符串才能做到逐字段等价。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ANONYMOUS_USER = "anonymous"


def _iso_now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


@dataclass
class User:
    id: str
    display_name: str = ""
    created_at: str = field(default_factory=_iso_now)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "display_name": self.display_name, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "User":
        return cls(
            id=data["id"],
            display_name=data.get("display_name", ""),
            created_at=data.get("created_at") or _iso_now(),
        )


@dataclass
class DatasetMeta:
    """数据集元信息。

    字段与原 upload_service.DatasetMeta 完全一致（属性访问方式不变），
    末尾追加 owner_id，保证既有调用方零改动。
    """

    dataset_id: str
    created_at: str = field(default_factory=_iso_now)
    files: list[str] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    profiles: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    owner_id: str = ANONYMOUS_USER

    @property
    def total_rows(self) -> int:
        return sum(t["row_count"] for t in self.tables)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "created_at": self.created_at,
            "files": self.files,
            "tables": self.tables,
            "profiles": self.profiles,
            "warnings": self.warnings,
            "owner_id": self.owner_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatasetMeta":
        return cls(
            dataset_id=data["dataset_id"],
            created_at=data.get("created_at") or _iso_now(),
            files=list(data.get("files") or []),
            tables=list(data.get("tables") or []),
            profiles=list(data.get("profiles") or []),
            warnings=list(data.get("warnings") or []),
            owner_id=data.get("owner_id") or ANONYMOUS_USER,
        )


@dataclass
class Metric:
    """指标语义层定义。

    expression 是可直接嵌入 SQL 的聚合表达式，例如 `ROUND(SUM(amount), 2)`。
    synonyms 用于命中判定（用户说"销售额/GMV/成交额"都指向同一条指标）。
    """

    name: str
    display_name: str
    expression: str
    description: str = ""
    required_columns: list[str] = field(default_factory=list)
    default_filter: str | None = None
    unit: str = ""
    synonyms: list[str] = field(default_factory=list)
    owner_id: str = ANONYMOUS_USER
    id: int | None = None
    created_at: str = field(default_factory=_iso_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "expression": self.expression,
            "description": self.description,
            "required_columns": self.required_columns,
            "default_filter": self.default_filter,
            "unit": self.unit,
            "synonyms": self.synonyms,
            "owner_id": self.owner_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Metric":
        return cls(
            name=data["name"],
            display_name=data.get("display_name") or data["name"],
            expression=data["expression"],
            description=data.get("description", ""),
            required_columns=list(data.get("required_columns") or []),
            default_filter=data.get("default_filter"),
            unit=data.get("unit", ""),
            synonyms=list(data.get("synonyms") or []),
            owner_id=data.get("owner_id") or ANONYMOUS_USER,
            id=data.get("id"),
            created_at=data.get("created_at") or _iso_now(),
        )


@dataclass
class AuditRecord:
    """一次查询的审计记录。"""

    task_id: str
    dataset_id: str
    owner_id: str
    question: str
    sql: str | None = None
    status: str = "failed"
    error_type: str | None = None
    retry_count: int = 0
    row_count: int = 0
    elapsed_ms: float = 0.0
    metric_check: dict[str, Any] | None = None
    id: int | None = None
    created_at: str = field(default_factory=_iso_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "dataset_id": self.dataset_id,
            "owner_id": self.owner_id,
            "question": self.question,
            "sql": self.sql,
            "status": self.status,
            "error_type": self.error_type,
            "retry_count": self.retry_count,
            "row_count": self.row_count,
            "elapsed_ms": self.elapsed_ms,
            "metric_check": self.metric_check,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditRecord":
        return cls(
            task_id=data["task_id"],
            dataset_id=data["dataset_id"],
            owner_id=data.get("owner_id") or ANONYMOUS_USER,
            question=data.get("question", ""),
            sql=data.get("sql"),
            status=data.get("status", "failed"),
            error_type=data.get("error_type"),
            retry_count=int(data.get("retry_count") or 0),
            row_count=int(data.get("row_count") or 0),
            elapsed_ms=float(data.get("elapsed_ms") or 0.0),
            metric_check=data.get("metric_check"),
            id=data.get("id"),
            created_at=data.get("created_at") or _iso_now(),
        )


@dataclass
class QAExample:
    """历史成功问答，用于（可选的）few-shot。

    默认不接入 Prompt：mock 引擎不读 Prompt，收益无法验证，
    且向量检索与项目一的 RAG 重叠。这里只负责存储与结构化检索。
    """

    question: str
    sql: str
    dataset_signature: str
    metric_name: str | None = None
    dimensions: list[str] = field(default_factory=list)
    success: bool = True
    use_count: int = 0
    id: int | None = None
    created_at: str = field(default_factory=_iso_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "sql": self.sql,
            "dataset_signature": self.dataset_signature,
            "metric_name": self.metric_name,
            "dimensions": self.dimensions,
            "success": self.success,
            "use_count": self.use_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QAExample":
        return cls(
            question=data["question"],
            sql=data["sql"],
            dataset_signature=data.get("dataset_signature", ""),
            metric_name=data.get("metric_name"),
            dimensions=list(data.get("dimensions") or []),
            success=bool(data.get("success", True)),
            use_count=int(data.get("use_count") or 0),
            id=data.get("id"),
            created_at=data.get("created_at") or _iso_now(),
        )
