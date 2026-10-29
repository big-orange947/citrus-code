"""Anthropic Messages conversion and incremental text/tool event parsing."""

from collections.abc import AsyncGenerator, AsyncIterator
import hashlib
import time
from typing import Literal, Optional, Union

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, TypeAdapter

from ..events import (
    AssistantMessageEvent, DoneEvent, StartEvent, TextDeltaEvent, TextEndEvent,
    TextStartEvent, ToolCallDeltaEvent, ToolCallEndEvent, ToolCallStartEvent,
)
from ..retry import stream_with_retries
from ..types import (
    AssistantMessage, ImageContent, ModelContext, ModelOptions, ModelSpec,
    SystemMessage, TextContent, ToolCall, ToolResultMessage, Usage, UserMessage,
)
from .transport import IncompleteStreamError, StreamAPIError, apply_extra_body, endpoint_url, error_event, request_headers, stream_sse


_ARGUMENTS = TypeAdapter(dict[str, JsonValue])
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def tool_id(value: str) -> str:
    # Historical calls can originate from another protocol. Keep both sides of
    # each call/result pair consistent with Anthropic's ID character limits.
    return "toolu_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]


def content_blocks(content: Union[str, list[Union[TextContent, ImageContent]]], images: bool) -> list[JsonValue]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks: list[JsonValue] = []
    for block in content:
        if isinstance(block, TextContent):
            if block.text:
                blocks.append({"type": "text", "text": block.text})
        elif images and block.mime_type in _IMAGE_TYPES:
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": block.mime_type, "data": block.data,
            }})
        else:
            blocks.append({"type": "text", "text": f"[图片未发送：当前模型未启用图片输入或不支持 {block.mime_type}]"})
    return blocks


def build_anthropic_request(model: ModelSpec, context: ModelContext, options: Optional[ModelOptions]) -> dict[str, JsonValue]:
    systems: list[str] = []
    messages: list[dict[str, JsonValue]] = []
    for message in context.messages:
        if isinstance(message, SystemMessage):
            systems.append(message.content if isinstance(message.content, str) else "\n".join(block.text for block in message.content))
            continue
        role = "assistant" if isinstance(message, AssistantMessage) else "user"
        blocks: list[JsonValue] = []
        if isinstance(message, (UserMessage, ToolResultMessage)):
            blocks = content_blocks(message.content, model.supports_images)
            if isinstance(message, ToolResultMessage):
                blocks = [{"type": "tool_result", "tool_use_id": tool_id(message.tool_call_id),
                           "content": blocks, "is_error": message.is_error}]
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextContent):
                    if block.text:
                        blocks.append({"type": "text", "text": block.text})
                else:
                    blocks.append({"type": "tool_use", "id": tool_id(block.id), "name": block.name, "input": block.arguments})
            if message.stop_reason == "length":
                blocks.append({"type": "text", "text": "[运行时提示：" + (message.error_message or "本条回复因长度限制提前结束；本条未完成的工具调用没有执行。") + "]"})
        if not blocks:
            continue
        if messages and messages[-1]["role"] == role:
            previous = messages[-1]["content"]
            if not isinstance(previous, list):
                raise TypeError("Expected content blocks")
            previous.extend(blocks)
        else:
            messages.append({"role": role, "content": blocks})
    if not messages:
        raise ValueError("At least one conversation message is required")
    maximum = model.max_output_tokens or 8192
    output_tokens = min(options.max_output_tokens, maximum) if options and options.max_output_tokens else maximum
    body: dict[str, JsonValue] = {"model": model.id, "messages": messages, "max_tokens": output_tokens, "stream": True}
    if systems:
        body["system"] = "\n\n".join(systems)
    if context.tools:
        body["tools"] = [{"name": tool.name, "description": tool.description, "input_schema": tool.parameters} for tool in context.tools]
    if options and options.temperature is not None:
        body["temperature"] = options.temperature
    return body


class _Usage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    cache_read_input_tokens: Optional[int] = Field(default=None, ge=0)
    cache_creation_input_tokens: Optional[int] = Field(default=None, ge=0)


class _Message(BaseModel):
    model_config = ConfigDict(extra="ignore")
    usage: Optional[_Usage] = None


class _Block(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, JsonValue] = Field(default_factory=dict)


class _Delta(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Optional[str] = None
    text: str = ""
    partial_json: str = ""
    stop_reason: Optional[str] = None


class _Error(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    message: str


class _Event(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    index: Optional[int] = Field(default=None, ge=0)
    message: Optional[_Message] = None
    content_block: Optional[_Block] = None
    delta: Optional[_Delta] = None
    usage: Optional[_Usage] = None
    error: Optional[_Error] = None


async def stream_anthropic_events(model: ModelSpec, data_stream: AsyncIterator[str]) -> AsyncGenerator[AssistantMessageEvent, None]:
    message = AssistantMessage(
        content=[], api=model.api, provider=model.provider, model=model.id,
        usage=None, stop_reason="pending", timestamp=int(time.time() * 1000),
    )
    started = False
    closed = False
    stop_reason: Optional[str] = None
    active: set[int] = set()
    arguments: dict[int, str] = {}
    initial_arguments: dict[int, dict[str, JsonValue]] = {}
    usage = _Usage()
    yield StartEvent(partial=message)
    try:
        async for data in data_stream:
            event = _Event.model_validate_json(data)
            if event.type == "error":
                error = event.error
                raise StreamAPIError(
                    f"{error.type}: {error.message}" if error else "Anthropic stream error",
                    retryable=error is not None and error.type in {"overloaded_error", "rate_limit_error", "api_error"},
                )
            if event.type == "ping":
                continue
            if event.type == "message_start":
                if started or event.message is None:
                    raise ValueError("Invalid or duplicate message_start")
                started = True
                if event.message.usage:
                    usage = event.message.usage
                continue
            if not started:
                raise ValueError("Missing message_start")
            if event.type == "content_block_start":
                index, block = event.index, event.content_block
                if stop_reason or index != len(message.content) or block is None:
                    raise ValueError("Invalid content block start")
                active.add(index)
                if block.type == "text":
                    message.content.append(TextContent(text=block.text))
                    yield TextStartEvent(content_index=index, partial=message)
                    if block.text:
                        yield TextDeltaEvent(content_index=index, delta=block.text, partial=message)
                elif block.type == "tool_use":
                    if not block.id or not block.name:
                        raise ValueError("Tool call requires an ID and name")
                    message.content.append(ToolCall(id=block.id, name=block.name, arguments={}))
                    initial_arguments[index] = block.input
                    arguments[index] = ""
                    yield ToolCallStartEvent(content_index=index, partial=message)
                else:
                    raise ValueError(f"Unsupported Anthropic content block: {block.type}. Disable thinking/server tools for this model.")
            elif event.type == "content_block_delta":
                index, delta = event.index, event.delta
                if index not in active or delta is None or stop_reason:
                    raise ValueError("Delta does not belong to an active content block")
                block = message.content[index]
                if delta.type == "text_delta" and isinstance(block, TextContent):
                    block.text += delta.text
                    yield TextDeltaEvent(content_index=index, delta=delta.text, partial=message)
                elif delta.type == "input_json_delta" and isinstance(block, ToolCall):
                    arguments[index] += delta.partial_json
                    yield ToolCallDeltaEvent(content_index=index, delta=delta.partial_json, partial=message)
                else:
                    raise ValueError("Unexpected content delta type")
            elif event.type == "content_block_stop":
                index = event.index
                if index not in active:
                    raise ValueError("Content block stopped without starting")
                active.remove(index)
                block = message.content[index]
                if isinstance(block, TextContent):
                    yield TextEndEvent(content_index=index, content=block.text, partial=message)
            elif event.type == "message_delta":
                if event.delta and event.delta.stop_reason is not None:
                    if stop_reason is not None or active:
                        raise ValueError("Invalid or duplicate stop reason")
                    stop_reason = event.delta.stop_reason
                if event.usage:
                    for name in event.usage.model_fields_set:
                        value = getattr(event.usage, name)
                        if value is not None:
                            setattr(usage, name, value)
            elif event.type == "message_stop":
                if stop_reason is None or active:
                    raise IncompleteStreamError("Message stopped before its content blocks and stop reason completed")
                closed = True
                break
        if not started or not closed:
            raise IncompleteStreamError("Anthropic stream ended without message_stop")
        reason: Literal["stop", "length", "toolUse"]
        if stop_reason in {"max_tokens", "model_context_window_exceeded"}:
            reason = "length"
            message.error_message = "模型达到输出或上下文长度限制，本条回复中的工具调用未执行。"
            message.content = [block for block in message.content if isinstance(block, TextContent)]
        elif stop_reason == "tool_use":
            reason = "toolUse"
            seen: set[str] = set()
            for index, block in enumerate(message.content):
                if isinstance(block, ToolCall):
                    if block.id in seen:
                        raise ValueError("Duplicate tool call ID")
                    seen.add(block.id)
                    block.arguments = _ARGUMENTS.validate_json(arguments[index]) if arguments[index] else initial_arguments[index]
            if not seen:
                raise ValueError("tool_use stop without tool calls")
            for index, block in enumerate(message.content):
                if isinstance(block, ToolCall):
                    yield ToolCallEndEvent(content_index=index, tool_call=block, partial=message)
        elif stop_reason in {"end_turn", "stop_sequence", "refusal"}:
            reason = "stop"
            if arguments:
                raise ValueError("Tool calls require the tool_use stop reason")
        else:
            raise ValueError(f"Unsupported Anthropic stop reason: {stop_reason}")
        input_tokens = (usage.input_tokens or 0) + (usage.cache_read_input_tokens or 0) + (usage.cache_creation_input_tokens or 0)
        output_tokens = usage.output_tokens or 0
        message.usage = Usage(
            input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens, cache_write_tokens=usage.cache_creation_input_tokens,
        )
        message.stop_reason = reason
        yield DoneEvent(reason=reason, message=message)
    except Exception as exc:
        yield error_event(message, exc)
    finally:
        if isinstance(data_stream, AsyncGenerator):
            await data_stream.aclose()


class AnthropicProvider:
    def __init__(
        self, client: httpx.AsyncClient, models: list[ModelSpec], api_key: SecretStr,
        *, provider_id: str = "anthropic", name: Optional[str] = None,
        auth_type: str = "x-api-key", headers: Optional[dict[str, str]] = None,
        extra_body: Optional[dict[str, JsonValue]] = None,
    ) -> None:
        if not models or any(model.provider != provider_id or model.api != "anthropic-messages" for model in models):
            raise ValueError("Models must belong to this Anthropic Messages provider")
        self.id, self.name = provider_id, name or provider_id
        self._client, self._api_key = client, api_key
        self._models = {model.id: model.model_copy(deep=True) for model in models}
        self._headers = {"anthropic-version": "2023-06-01", **(headers or {})}
        self._auth_type = auth_type
        self._extra_body = dict(extra_body or {})

    def get_models(self) -> list[ModelSpec]:
        return [model.model_copy(deep=True) for model in self._models.values()]

    def stream(self, model: ModelSpec, context: ModelContext, options: Optional[ModelOptions] = None) -> AsyncGenerator[AssistantMessageEvent, None]:
        if self._models.get(model.id) != model:
            raise ValueError("The requested model is not registered with this provider")
        body = build_anthropic_request(model, context, options)
        apply_extra_body(body, self._extra_body)
        headers = request_headers(self._api_key, self._auth_type, self._headers)
        return stream_with_retries(lambda: stream_anthropic_events(
            model, stream_sse(self._client, endpoint_url(model.base_url, model.api), headers, body),
        ))
