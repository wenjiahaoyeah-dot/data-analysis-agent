"""全局配置。

所有配置项均可通过环境变量或项目根目录的 .env 文件覆盖。
未配置 LLM_API_KEY 时自动降级为 Mock LLM，保证项目在离线环境下依然可以
完整跑通「上传 -> 生成 SQL -> 执行 -> 分析 -> 出图」的闭环。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, ""))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, ""))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    app_name: str = "Intelligent Data Analysis Agent"
    version: str = "2.0.0"

    # ---- 存储 ----
    storage_dir: Path = field(default_factory=lambda: Path(os.getenv("STORAGE_DIR", BASE_DIR / "storage")))
    max_upload_mb: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_MB", 50))

    # ---- LLM ----
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    )
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-flash"))
    llm_timeout: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT", 60.0))
    llm_temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.0))
    llm_max_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 2048))
    # 禁用真实模型调用（无 key 时也视为不可用）。
    force_mock: bool = field(default_factory=lambda: _env_bool("USE_MOCK_LLM", False))

    # ---- Agent 执行约束 ----
    sql_timeout_seconds: int = field(default_factory=lambda: _env_int("SQL_TIMEOUT_SECONDS", 15))
    max_result_rows: int = field(default_factory=lambda: _env_int("MAX_RESULT_ROWS", 500))
    agent_max_tool_calls: int = field(default_factory=lambda: _env_int("AGENT_MAX_TOOL_CALLS", 10))
    agent_max_sql_calls: int = field(default_factory=lambda: _env_int("AGENT_MAX_SQL_CALLS", 4))
    agent_max_model_calls: int = field(default_factory=lambda: _env_int("AGENT_MAX_MODEL_CALLS", 8))
    agent_timeout_seconds: int = field(default_factory=lambda: _env_int("AGENT_TIMEOUT_SECONDS", 150))

    # ---- 控制面（元数据 / 任务 / 报告 / 指标 / 审计） ----
    # json  = 本地文件，零依赖，测试与演示默认
    # postgres = 生产模式，需要 POSTGRES_DSN
    repository_backend: str = field(
        default_factory=lambda: os.getenv("REPOSITORY_BACKEND", "json").strip().lower()
    )
    postgres_dsn: str = field(default_factory=lambda: os.getenv("POSTGRES_DSN", ""))
    postgres_schema: str = field(default_factory=lambda: os.getenv("POSTGRES_SCHEMA", "public"))
    postgres_pool_size: int = field(default_factory=lambda: _env_int("POSTGRES_POOL_SIZE", 5))

    # ---- 身份与功能开关 ----
    # true 时请求必须提供配置的有效 Key，否则 401。
    require_api_key: bool = field(default_factory=lambda: _env_bool("REQUIRE_API_KEY", False))
    api_keys: list[str] = field(default_factory=lambda: [
        key.strip() for key in (os.getenv("API_KEYS", "").strip() or os.getenv("API_KEY", "")).split(",")
        if key.strip()
    ])
    # 指标语义层（Prompt 注入 + 事后校验）
    metrics_enabled: bool = field(default_factory=lambda: _env_bool("METRICS_ENABLED", True))
    # 查询审计节点
    audit_enabled: bool = field(default_factory=lambda: _env_bool("AUDIT_ENABLED", True))
    # few-shot 默认关闭：mock 下无法验证收益，且与项目一 RAG 重叠
    few_shot_enabled: bool = field(default_factory=lambda: _env_bool("FEW_SHOT_ENABLED", False))

    @property
    def datasets_dir(self) -> Path:
        return self.storage_dir / "datasets"

    @property
    def tasks_dir(self) -> Path:
        return self.storage_dir / "tasks"

    @property
    def use_mock_llm(self) -> bool:
        """没有 API Key 时自动进入 Mock 模式。"""
        return self.force_mock or not self.llm_api_key.strip()

    def ensure_dirs(self) -> None:
        self.datasets_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
