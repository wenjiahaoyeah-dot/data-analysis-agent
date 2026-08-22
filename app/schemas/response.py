"""API 请求 / 响应模型。

所有对外结构都用 Pydantic 定义，好处是：
1. 接口契约明确，前端可以直接照着写；
2. 响应里的字段类型（尤其是 Decimal / date）在这里统一转换成 JSON 友好格式；
3. 出问题时 FastAPI 会自动返回 422 与字段级错误信息。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------- #
# 通用
# ---------------------------------------------------------------------- #
class ColumnInfo(BaseModel):
    name: str
    type: str


class TableInfo(BaseModel):
    table_name: str
    row_count: int
    columns: list[ColumnInfo]


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    null_count: int
    null_ratio: float
    distinct_count: int
    stats: dict[str, Any] | None = None
    samples: list[str] | None = None


class TableProfile(BaseModel):
    table_name: str
    row_count: int
    column_count: int
    duplicate_rows: int
    columns: list[ColumnProfile]


# ---------------------------------------------------------------------- #
# 上传 / Schema
# ---------------------------------------------------------------------- #
class UploadResponse(BaseModel):
    dataset_id: str
    tables: list[TableInfo]
    files: list[str]
    total_rows: int
    profiles: list[TableProfile]
    warnings: list[str] = Field(default_factory=list)
    created_at: str


class SchemaResponse(BaseModel):
    dataset_id: str
    tables: list[TableInfo]
    created_at: str


# ---------------------------------------------------------------------- #
# 分析任务
# ---------------------------------------------------------------------- #
class AnalyzeRequest(BaseModel):
    dataset_id: str = Field(..., pattern=r"^[0-9a-f]{12}$", description="上传接口返回的数据集 ID")
    question: str = Field(..., min_length=1, max_length=500, description="自然语言分析需求")
    mode: Literal["agent"] = "agent"
    session_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class ChartConfig(BaseModel):
    type: str = "table"
    x: str | None = None
    y: str | None = None
    title: str = ""
    x_label: str | None = None
    y_label: str | None = None


class TraceStep(BaseModel):
    node: str
    status: str
    detail: str = ""
    elapsed_ms: float = 0.0
    engine: str | None = None
    query_elapsed_ms: float | None = None


class RepairRecord(BaseModel):
    attempt: int
    failed_sql: str | None = None
    error: str | None = None
    error_type: str | None = None
    fixed_sql: str | None = None
    ok: bool = False
    note: str | None = None


class MetricCheck(BaseModel):
    status: Literal["not_applicable", "pass", "info", "deviation"] = "not_applicable"
    metrics: list[str] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class AnalyzeResponse(BaseModel):
    task_id: str
    dataset_id: str
    question: str
    status: Literal["success", "failed", "running", "needs_clarification"] = "success"
    mode: Literal["workflow", "agent"] = "workflow"
    session_id: str | None = None
    clarification: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    queries: list[dict[str, Any]] = Field(default_factory=list)
    selected_result_id: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)

    sql: str | None = None
    sql_explanation: str | None = None
    columns: list[str] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False

    analysis: str | None = None
    insights: list[str] = Field(default_factory=list)
    key_metrics: dict[str, Any] = Field(default_factory=dict)
    chart: ChartConfig | None = None
    chart_result_id: str | None = None
    charts: list[dict[str, Any]] = Field(default_factory=list)
    chart_figure: dict[str, Any] | None = None

    plan: dict[str, Any] | None = None
    metric_check: MetricCheck | None = None
    trace: list[TraceStep] = Field(default_factory=list)
    repair_history: list[RepairRecord] = Field(default_factory=list)
    retry_count: int = 0
    error: str | None = None
    error_type: str | None = None
    elapsed_ms: float = 0.0
    llm_mode: str = "unknown"
    model_name: str = "unknown"
    created_at: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    question: str
    dataset_id: str
    created_at: str
    elapsed_ms: float = 0.0
    row_count: int = 0
    error: str | None = None


class ReportResponse(BaseModel):
    task_id: str
    markdown: str
    download_url: str
