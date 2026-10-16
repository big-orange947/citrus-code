import time
from typing import Literal

import json
from collections.abc import AsyncIterator

import httpx
from pydantic import JsonValue, SecretStr

from ..types import (
    AssistantMessage,
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


def to_text(content: str | list[TextContent]) -> str:
    if isinstance(content, str):
        return content

    parts: list[str] = []

    for block in content:
        parts.append(block.text)

    return "".join(parts)


def to_chat_message(message: Message) -> dict[str, JsonValue]:
    if isinstance(message,(SystemMessage, UserMessage)):
        return {
            "role": message.role,
            "content": to_text(message.content)
        }

    if isinstance(message,ToolResultMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,    
            "content": to_text(message.content),
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

    for message in context.messages:
        messages.append(to_chat_message(message))

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

        raise RuntimeError(
            "The chat stream ended without a [DONE] marker"
        )

async def stream_assistant_events(
    model: ModelSpec,
    data_stream: AsyncIterator[str],
) -> AsyncIterator[AssistantMessageEvent]:
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
            raise RuntimeError(
                "The response ended without a finish reason"
            )

        reason: Literal["stop", "length", "toolUse"]

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
            if tool_buffers:
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

            elif isinstance(block, ToolCall):
                yield ToolCallEndEvent(
                    content_index=content_index,
                    tool_call=block,
                    partial=message,
                )

        message.stop_reason = reason

        yield DoneEvent(
            reason=reason,
            message=message,
        )

    except Exception as exc:
        message.stop_reason = "error"
        message.error_message = str(exc)

        yield ErrorEvent(
            reason="error",
            error=message,
        )

    