"""Retry an uncommitted model response; tool execution stays outside this layer."""
from typing import Optional

import asyncio
from collections.abc import AsyncGenerator, Callable
from ..compat import aclosing

from .events import AssistantMessageEvent, DoneEvent, ErrorEvent, RetryEvent


async def stream_with_retries(
    create_stream: Callable[[], AsyncGenerator[AssistantMessageEvent, None]],
    *,
    max_retries: int = 3,
) -> AsyncGenerator[AssistantMessageEvent, None]:
    if max_retries < 0:
        raise ValueError("max_retries must not be negative")
    for attempt in range(max_retries + 1):
        failure: Optional[ErrorEvent] = None
        async with aclosing(create_stream()) as stream:
            async for event in stream:
                if isinstance(event, ErrorEvent):
                    failure = event
                    break
                yield event
                if isinstance(event, DoneEvent):
                    return

        if failure is None:
            raise RuntimeError("The model stream ended without a terminal event")
        if not failure.retryable or failure.reason != "error" or attempt == max_retries:
            yield failure
            return

        delay = min(30.0, max(2.0 ** (attempt + 1), failure.retry_after_seconds or 0.0))
        yield RetryEvent(
            attempt=attempt + 1,
            max_retries=max_retries,
            delay_seconds=delay,
            error_message=failure.error.error_message or "模型请求暂时失败",
        )
        # Cancellation propagates, including while waiting between attempts.
        await asyncio.sleep(delay)
