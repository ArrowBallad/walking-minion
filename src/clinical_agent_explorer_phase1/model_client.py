from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

from .models import AgentDecision, ToolCall


class AgentClient(Protocol):
    def decide(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AgentDecision: ...


class DeepSeekClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com"
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL") or "deepseek-chat"
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        if not self.api_key:
            raise ValueError("需要设置环境变量 DEEPSEEK_API_KEY")
        if max_attempts < 1:
            raise ValueError("max_attempts 必须为正整数")

    def decide(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AgentDecision:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "thinking": {"type": "disabled"},
            }
        ).encode("utf-8")
        body: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    parsed = json.loads(response.read().decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise ValueError("响应顶层不是 JSON 对象")
                    body = parsed
                    break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
                if exc.code not in {408, 429, 500, 502, 503, 504}:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
            if attempt < self.max_attempts:
                time.sleep(2 ** (attempt - 1))
        if body is None:
            raise RuntimeError(
                f"模型请求在 {self.max_attempts} 次尝试后失败：{last_error}"
            ) from last_error

        try:
            choice = body["choices"][0]
            message = choice["message"]
            calls = []
            for raw_call in message.get("tool_calls") or []:
                function = raw_call["function"]
                arguments = json.loads(function["arguments"])
                if not isinstance(arguments, dict):
                    raise ValueError("工具参数必须解析为 JSON 对象")
                calls.append(
                    ToolCall(
                        call_id=str(raw_call["id"]),
                        name=str(function["name"]),
                        arguments=arguments,
                    )
                )
            return AgentDecision(
                content=message.get("content"),
                tool_calls=calls,
                response_metadata={
                    "response_id": body.get("id"),
                    "model": body.get("model"),
                    "created": body.get("created"),
                    "finish_reason": choice.get("finish_reason"),
                    "usage": body.get("usage"),
                    "reasoning_content_omitted": bool(message.get("reasoning_content")),
                },
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"模型响应格式无效：{exc}") from exc
