"""Shared HTTP/SSE transport and retry classification for model protocols."""

from collections.abc import AsyncGenerator
from typing import Optional

import httpx
from pydantic import JsonValue, SecretStr

from ..events import ErrorEvent
from ..types import AssistantMessage


class IncompleteStreamError(RuntimeError):
    """The server closed a response before its protocol completion marker."""


class StreamAPIError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def apply_extra_body(body: dict[str, JsonValue], extra: dict[str, JsonValue]) -> None:
    if {"model", "messages", "tools", "stream", "system"}.intersection(extra):
        raise ValueError("Extra parameters cannot replace routing, conversation or tool definitions")
    body.update(extra)


def endpoint_url(base_url: str, api: str) -> str:
    base = base_url.rstrip("/")
    if api == "anthropic-messages":
        if base.endswith("/messages"):
            return base
        return base + ("/messages" if base.endswith("/v1") else "/v1/messages")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def request_headers(api_key: SecretStr, auth_type: str, extra: dict[str, str]) -> dict[str, str]:
    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    key = api_key.get_secret_value()
    if key and auth_type == "bearer":
        headers["Authorization"] = "Bearer " + key
    elif key and auth_type == "x-api-key":
        headers["x-api-key"] = key
    # A case-insensitive mapping lets a relay override authentication headers.
    merged = httpx.Headers(headers)
    merged.update(extra)
    return dict(merged)


async def stream_sse(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], body: dict[str, JsonValue],
) -> AsyncGenerator[str, None]:
    async with client.stream("POST", url, headers=headers, json=body) as response:
        response.raise_for_status()
        response.encoding = "utf-8"
        data: list[str] = []
        async for line in response.aiter_lines():
            if not line:
                if data:
                    yield "\n".join(data)
                    data.clear()
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if field == "data":
                data.append(value[1:] if value.startswith(" ") else value)
        if data:
            yield "\n".join(data)


def error_event(message: AssistantMessage, exc: Exception) -> ErrorEvent:
    message.stop_reason = "error"
    retryable = isinstance(exc, (
        httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, IncompleteStreamError,
    )) or isinstance(exc, StreamAPIError) and exc.retryable
    retry_after: Optional[float] = None
    if isinstance(exc, httpx.HTTPStatusError):
        retryable = exc.response.status_code in (408, 429, 500, 502, 503, 504, 529)
        header = exc.response.headers.get("Retry-After", "").strip()
        if header.isascii() and header.isdigit():
            retry_after = min(30.0, float(header))
    detail = str(exc).strip() or "模型调用失败，接口未提供详细说明。"
    if isinstance(exc, (httpx.RemoteProtocolError, IncompleteStreamError)):
        detail = "模型响应未完整接收，连接已中断。本条回复中的工具调用未执行。原始错误：" + detail
    elif isinstance(exc, httpx.ReadTimeout):
        detail = "等待模型响应数据超时；此前已完成的工具操作保留，可用 --model-timeout 调整空闲超时。"
    message.error_message = f"{type(exc).__name__}: {detail}"
    return ErrorEvent(reason="error", error=message, retryable=retryable, retry_after_seconds=retry_after)
