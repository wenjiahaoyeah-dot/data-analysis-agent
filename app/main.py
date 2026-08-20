"""FastAPI 应用入口。

启动方式：
    uvicorn app.main:app --reload --port 8000
接口文档：
    http://127.0.0.1:8000/docs
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.api import analyze, audit, metrics, report, upload
from app.config import settings
from app.repository.factory import reset_repository


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时预热控制面连接，关闭时释放连接池。"""
    from app.repository.factory import get_repository

    if settings.require_api_key and not settings.api_keys:
        raise RuntimeError("REQUIRE_API_KEY=true 时必须配置 API_KEYS 或 API_KEY")
    try:
        get_repository().ping()
    except Exception as exc:  # 连不上就让启动失败，避免带病运行
        raise RuntimeError(f"控制面初始化失败：{exc}") from exc
    yield
    reset_repository()


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    lifespan=lifespan,
    description=(
        "基于 LangGraph 的智能数据分析 Agent：自然语言 -> Schema 感知 -> Text-to-SQL "
        "-> AST 安全校验 -> 受限执行 -> 错误自动修复 -> 结果分析与可视化。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(analyze.router)
app.include_router(report.router)
app.include_router(metrics.router)
app.include_router(audit.router)


TOOL_AGENT_MERMAID = """graph TD
    START([用户问题及会话历史]) --> model[模型决定下一步]
    model -- 工具调用 --> tools[校验并顺序执行工具]
    tools -- 继续 --> model
    tools -- 澄清 / 预算耗尽 / 超时 --> END([本轮结束])
    model -- 最终答复 / 错误 --> END
"""


@app.get("/api/health", tags=["system"], summary="健康检查")
async def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.version,
        "llm_mode": "unconfigured" if settings.use_mock_llm else "real",
        "llm_model": settings.llm_model if not settings.use_mock_llm else None,
        "sql_timeout_seconds": settings.sql_timeout_seconds,
        "max_result_rows": settings.max_result_rows,
        "max_upload_mb": settings.max_upload_mb,
        "agent_limits": {"tools": settings.agent_max_tool_calls, "sql": settings.agent_max_sql_calls,
                         "models": settings.agent_max_model_calls, "seconds": settings.agent_timeout_seconds},
    }


@app.get("/api/agent/graph", tags=["system"], summary="返回 Agent 工作流拓扑（Mermaid）")
async def agent_graph(mode: Literal["agent"] = "agent"):
    from app.agent.tool_agent import TOOLS
    return {"mermaid": TOOL_AGENT_MERMAID, "nodes": ["model", "tools"],
            "tools": [tool["function"]["name"] for tool in TOOLS]}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{settings.app_name}</title>
  <style>
    body {{ font-family: -apple-system, "PingFang SC", sans-serif; max-width: 760px;
            margin: 60px auto; padding: 0 24px; color: #1f2328; line-height: 1.7; }}
    h1 {{ font-size: 26px; margin-bottom: 4px; }}
    .sub {{ color: #6b7280; margin-bottom: 28px; }}
    a {{ color: #2563eb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 18px 20px; margin: 16px 0; }}
    li {{ margin: 6px 0; }}
  </style>
</head>
<body>
  <h1>{settings.app_name}</h1>
  <div class="sub">自然语言驱动的数据分析 Agent · LangGraph + DuckDB + Text-to-SQL</div>

  <div class="card">
    <b>快速入口</b>
    <ul>
      <li><a href="/docs">Swagger 接口文档</a> —— 可直接在页面上传数据、发起分析</li>
      <li><a href="/api/agent/graph">Agent 工作流拓扑</a></li>
      <li><a href="/api/health">健康检查（含当前推理引擎模式）</a></li>
    </ul>
  </div>

  <div class="card">
    <b>当前配置</b>
    <ul>
      <li>推理引擎：<code>{"未配置模型" if settings.use_mock_llm else settings.llm_model}</code></li>
      <li>每轮 SQL 尝试上限：<code>{settings.agent_max_sql_calls}</code></li>
      <li>查询超时：<code>{settings.sql_timeout_seconds}s</code>，结果行数上限：<code>{settings.max_result_rows}</code></li>
    </ul>
  </div>

  <div class="card">
    <b>可视化界面</b>
    <p>启动 Streamlit 前端：<code>streamlit run frontend/streamlit_app.py</code></p>
  </div>
</body>
</html>
"""
