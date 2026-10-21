import time
from typing import Literal

import json
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Iterable,
)

import httpx
from pydantic import JsonValue, SecretStr

from ..types import (
    AssistantMessage,
    ImageContent,
    Message,
    SystemMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    ModelContext,
    ModelOptions,
    ModelSpec,
    Usage,
)

from ..events import (
    AssistantMessageEvent,
    DoneEvent,
    ErrorEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

from .tool_call_buffer import ToolCallBuffer

from .chat_stream_types import ChatStreamChunk


class IncompleteStreamError(RuntimeError):
    """The response ended before its required completion markers arrived."""


def to_text(content: str | Iterable[TextContent | ImageContent]) -> str:
    if isinstance(content, str):
        return content

    parts: list[str] = []

    for block in content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        else:
            parts.append("[Image attachment]")

    return "".join(parts)


def image_part(block: ImageContent) -> dict[str, JsonValue]:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
    }


def to_chat_message(
    message: Message, *, supports_images: bool = False
) -> dict[str, JsonValue]:
    if isinstance(message, SystemMessage):
        return {
            "role": message.role,
            "content": to_text(message.content)
        }

    if isinstance(message, UserMessage):
        if isinstance(message.content, str):
            return {"role": "user", "content": message.content}
        has_images = any(isinstance(block, ImageContent) for block in message.content)
        if not has_images:
            return {"role": "user", "content": to_text(message.content)}
        if not supports_images:
            return {
                "role": "user",
                "content": to_text(message.content) + "\n[Images omitted: this model configuration does not support images.]",
            }
        parts: list[JsonValue] = []
        for block in message.content:
            if isinstance(block, TextContent):
                parts.append({"type": "text", "text": block.text})
            else:
                parts.append(image_part(block))
        return {"role": "user", "content": parts}

    if isinstance(message, ToolResultMessage):
        text = to_text(message.content)
        if not supports_images and any(isinstance(block, ImageContent) for block in message.content):
            text += "\n[Image omitted: this model configuration does not support images.]"
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": text,
        }

    if isinstance(message, AssistantMessage):
        text_parts: list[str] = []
        tool_calls: list[JsonValue] = []

        for block in message.content:
            if isinstance(block,TextContent):
                text_parts.append(block.text)

            elif isinstance(block, ToolCall):
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(
                                block.arguments,
                                ensure_ascii=False,
                                allow_nan=False,
                            ),
                        },
                    }
                )

            else:
                raise TypeError(
                    "Unsupported assistant content type"
                )

        if message.stop_reason == "length":
            diagnostic = message.error_message or "本条回复因长度限制提前结束，内容不完整。"
            text_parts.append(f"\n\n[运行时提示：{diagnostic}]")

        result: dict[str, JsonValue] = {
            "role": "assistant",
            "content": "".join(text_parts),
        }

        if tool_calls:
            result["tool_calls"] = tool_calls

        return result

    raise TypeError("Unsupported message type")

def build_chat_request(
        model: ModelSpec,
        context: ModelContext,
        options: ModelOptions | None = None,
) -> dict[str, JsonValue]:
    if not context.messages:
        raise ValueError("At least one message is required")

    messages: list[JsonValue] = []

    pending_images: list[JsonValue] = []
    for index, message in enumerate(context.messages):
        messages.append(to_chat_message(message, supports_images=model.supports_images))
        if isinstance(message, ToolResultMessage) and model.supports_images:
            pending_images.extend(
                image_part(block)
                for block in message.content
                if isinstance(block, ImageContent)
            )
            last_tool_result = (
                index + 1 == len(context.messages)
                or not isinstance(context.messages[index + 1], ToolResultMessage)
            )
            if pending_images and last_tool_result:
                # All tool replies in a batch must precede the synthetic user
                # image message, matching pi's Chat Completions conversion.
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Attached image(s) from tool results:"},
                        *pending_images,
                    ],
                })
                pending_images = []

    body: dict[str, JsonValue] = {
        "model": model.id,
        "messages": messages,
        "stream": True,
        "stream_options": {
            "include_usage": True,
        },
    }

    if context.tools:
        tools: list[JsonValue] = []

        for tool in context.tools:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    }
                }
            )

        body["tools"] = tools

    if options is not None:
        if options.max_output_tokens is not None:
            body["max_tokens"] = options.max_output_tokens

        if options.temperature is not None:
            body["temperature"] = options.temperature

    return body


async def stream_chat_data(
        client: httpx.AsyncClient,
        model: ModelSpec,
        api_key: SecretStr,
        body: dict[str, JsonValue],
) -> AsyncIterator[str]:
    url=model.base_url.rstrip("/") + "/chat/completions"


    headers = {
        "Authorization": f"Bearer {api_key.get_secret_value()}",
        "Accept": "text/event-stream",
    }


    async with client.stream(
        "POST",
        url,
        headers=headers,
        json=body,
    ) as response:
        response.raise_for_status()
        response.encoding = "utf-8"

        data_lines: list[str] = []

        async for line in response.aiter_lines():
            if line == "":
                if not data_lines:
                    continue

                data = "\n".join(data_lines)
                data_lines.clear()

                if data == "[DONE]":
                    return 

                yield data
                continue

            if line.startswith(":"):
                continue

            field, _, value = line.partition(":")

            if field == "data":
                if value.startswith(" "):
                    value = value[1:]

                data_lines.append(value)

        raise IncompleteStreamError(
            "The chat stream ended without a [DONE] marker"
        )

async def stream_assistant_events(
    model: ModelSpec,
    data_stream: AsyncIterator[str],
) -> AsyncGenerator[AssistantMessageEvent, None]:
    message = AssistantMessage(
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=None,
        stop_reason="pending",
        timestamp=int(time.time() * 1000),
    )

    text_block: TextContent | None = None
    text_content_index = 0
    tool_buffers: dict[int, ToolCallBuffer] = {}

    finish_reason: Literal[
        "stop", "length", "tool_calls"
    ] | None = None

    yield StartEvent(partial=message)

    try:
        async for data in data_stream:
            chunk = ChatStreamChunk.model_validate_json(data)

            if chunk.error is not None:
                raise RuntimeError(
                    f"The API returned an error: {chunk.error}"
                )

            if chunk.usage is not None:
                message.usage = Usage(
                    input_tokens=chunk.usage.prompt_tokens,
                    output_tokens=chunk.usage.completion_tokens,
                    total_tokens=chunk.usage.total_tokens,
                    cache_read_tokens=(
                        chunk.usage.prompt_cache_hit_tokens
                    ),
                )

            for choice in chunk.choices:
                if choice.index != 0:
                    raise RuntimeError(
                        "Only one response choice is supported"
                    )

                delta = choice.delta

                if delta.reasoning_content:
                    raise NotImplementedError(
                        "Thinking content is not implemented yet"
                    )

                if finish_reason is not None:
                    if delta.content or delta.tool_calls:
                        raise RuntimeError(
                            "Received content after the response finished"
                        )

                if delta.content:
                    if text_block is None:
                        text_content_index = len(message.content)
                        text_block = TextContent(text="")
                        message.content.append(text_block)

                        yield TextStartEvent(
                            content_index=text_content_index,
                            partial=message,
                        )

                    text_block.text += delta.content

                    yield TextDeltaEvent(
                        content_index=text_content_index,
                        delta=delta.content,
                        partial=message,
                    )

                if delta.tool_calls:
                    for tool_delta in delta.tool_calls:
                        buffer = tool_buffers.get(tool_delta.index)
                        is_new = buffer is None

                        if buffer is None:
                            buffer = ToolCallBuffer(
                                tool_index=tool_delta.index,
                                content_index=len(message.content),
                            )

                            tool_buffers[tool_delta.index] = buffer
                            message.content.append(buffer.tool_call)

                        fragment = buffer.append(tool_delta)

                        if is_new:
                            yield ToolCallStartEvent(
                                content_index=buffer.content_index,
                                partial=message,
                            )

                        if fragment:
                            yield ToolCallDeltaEvent(
                                content_index=buffer.content_index,
                                delta=fragment,
                                partial=message,
                            )

                if choice.finish_reason is not None:
                    if choice.finish_reason not in (
                        "stop",
                        "length",
                        "tool_calls",
                    ):
                        raise RuntimeError(
                            "Unsupported finish reason: "
                            f"{choice.finish_reason}"
                        )

                    finish_reason = choice.finish_reason

        if finish_reason is None:
            raise IncompleteStreamError(
                "The response ended without a finish reason"
            )

        reason: Literal["stop", "length", "toolUse"]
        truncated_tool_calls = finish_reason == "length" and bool(tool_buffers)

        if finish_reason == "tool_calls":
            if not tool_buffers:
                raise RuntimeError(
                    "The response finished with tool_calls "
                    "but contained no tool calls"
                )

            seen_ids: set[str] = set()

            for buffer in tool_buffers.values():
                tool_call = buffer.finish()

                if tool_call.id in seen_ids:
                    raise ValueError("Duplicate tool call ID")

                seen_ids.add(tool_call.id)

            reason = "toolUse"

        else:
            if truncated_tool_calls:
                names = ", ".join(
                    buffer.tool_call.name or "unknown"
                    for buffer in tool_buffers.values()
                )
                message.error_message = (
                    f"工具参数生成时回复被截断（{names}）。"
                    "本条回复中的工具调用均未执行。"
                    "请提高 --max-output-tokens 后重试，"
                    "或将较大的文件分批写入。"
                )
            elif tool_buffers:
                raise RuntimeError(
                    "Tool calls did not finish normally: "
                    f"{finish_reason}"
                )

            reason = finish_reason

        for content_index, block in enumerate(message.content):
            if isinstance(block, TextContent):
                yield TextEndEvent(
                    content_index=content_index,
                    content=block.text,
                    partial=message,
                )

            elif isinstance(block, ToolCall) and not truncated_tool_calls:
                yield ToolCallEndEvent(
                    content_index=content_index,
                    tool_call=block,
                    partial=message,
                )

        if truncated_tool_calls:
            # A length stop cannot authorize execution, even when one call in
            # the batch happens to contain parseable JSON. Keep only text in
            # the committed history, so no orphan or partial call is replayed.
            # Close text blocks before removing calls to preserve event indices.
            message.content = [
                block for block in message.content if isinstance(block, TextContent)
            ]

        message.stop_reason = reason

        yield DoneEvent(
            reason=reason,
            message=message,
        )

    except Exception as exc:
        message.stop_reason = "error"
        retryable = isinstance(exc, (
            httpx.TimeoutException, httpx.NetworkError,
            httpx.RemoteProtocolError, IncompleteStreamError,
        ))
        retry_after: float | None = None
        if isinstance(exc, httpx.HTTPStatusError):
            retryable = exc.response.status_code in (408, 429, 500, 502, 503, 504)
            header = exc.response.headers.get("Retry-After", "").strip()
            if header.isascii() and header.isdigit():
                retry_after = min(30.0, float(header))
        detail = str(exc).strip()
        if isinstance(exc, (httpx.RemoteProtocolError, IncompleteStreamError)):
            detail = (
                "模型接口的流式响应未完整接收，连接就已中断。"
                "可能与服务端、代理或网络有关，不能仅凭此错误确定具体来源。"
                "此前已完成的工具操作不受影响；本条未完整回复中的工具调用未执行。"
                "可以继续当前会话，先检查实际文件状态，再接着完成任务。"
                f" 原始错误：{detail or '服务端响应传输不完整。'}"
            )
        elif isinstance(exc, httpx.ReadTimeout):
            detail = (
                "等待模型响应数据超时（ReadTimeout）。"
                "这是模型接口读取超时，不代表上一条工具命令失败。"
                "CLI 可通过 --model-timeout 增加等待时间。"
            )
        elif isinstance(exc, httpx.ConnectTimeout):
            detail = "连接模型服务超时，请检查网络、代理和接口地址。"
        elif isinstance(exc, httpx.WriteTimeout):
            detail = "向模型服务发送请求超时，请检查网络及图片等请求内容的大小。"
        elif isinstance(exc, httpx.PoolTimeout):
            detail = "等待模型 HTTP 连接池超时。"
        message.error_message = f"{type(exc).__name__}: {detail or '模型调用失败，异常未提供详细文字。'}"

        yield ErrorEvent(
            reason="error",
            error=message,
            retryable=retryable,
            retry_after_seconds=retry_after,
        )

    finally:
        if isinstance(data_stream, AsyncGenerator):
            await data_stream.aclose()
