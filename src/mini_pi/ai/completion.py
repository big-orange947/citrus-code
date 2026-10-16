from .events import DoneEvent, ErrorEvent
from .provider import Provider
from .types import (
    AssistantMessage,
    ModelContext,
    ModelOptions,
    ModelSpec,
)


async def complete(
        provider: Provider,
        model: ModelSpec,
        context: ModelContext,
        options: ModelOptions | None = None,
) -> AssistantMessage:
    async for event in provider.stream(
        model,
        context,
        options,
    ):
        if isinstance(event,DoneEvent):
            return event.message

        if isinstance(event,ErrorEvent):
            return event.error

    raise RuntimeError(
        "The model event stream ended without a final result"
    )