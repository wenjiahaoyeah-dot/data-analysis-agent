"""OpenAI 兼容工具调用客户端；无模型配置时明确拒绝，不降级。"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import settings


class LLMError(RuntimeError):
    """模型调用或输出异常。"""


class BaseLLM:
    name = "unconfigured"

    def complete_with_tools(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                            timeout: float | None = None) -> dict[str, Any]:
        raise LLMError("数据分析需要支持 Function Calling 的真实模型，请配置模型服务")


class OpenAICompatLLM(BaseLLM):
    name = "openai-compatible"

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def complete_with_tools(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                            timeout: float | None = None) -> dict[str, Any]:
        body = {"model": self.model, "messages": messages, "tools": tools,
                "tool_choice": "auto", "temperature": settings.llm_temperature,
                "max_tokens": settings.llm_max_tokens, "stream": False}
        try:
            with httpx.Client(timeout=min(self.timeout, timeout) if timeout is not None else self.timeout) as client:
                response = client.post(self._endpoint(), headers={"Authorization": f"Bearer {self.api_key}"}, json=body)
            if response.status_code >= 400:
                raise LLMError(f"模型服务返回 HTTP {response.status_code}；请检查模型、额度和工具调用支持")
            data = response.json()
            choice = (data.get("choices") or [])[0]
            message = choice.get("message")
            if not isinstance(message, dict) or not (message.get("content") or message.get("tool_calls")):
                raise LLMError("模型未返回有效内容或工具调用")
            return {"message": message, "usage": data.get("usage") or {},
                    "finish_reason": choice.get("finish_reason")}
        except (httpx.HTTPError, json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
            raise LLMError(f"模型调用失败：{type(exc).__name__}") from exc


_llm_cache: dict[str, BaseLLM] = {}


def get_llm() -> BaseLLM:
    """缺少模型时返回不可用客户端；不使用离线规则引擎替代。"""
    mode = "unconfigured" if settings.use_mock_llm else "real"
    if mode not in _llm_cache:
        _llm_cache[mode] = BaseLLM() if mode == "unconfigured" else OpenAICompatLLM()
    return _llm_cache[mode]
