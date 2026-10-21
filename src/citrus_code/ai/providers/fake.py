import asyncio
import time
from collections.abc import AsyncIterator

from ..events import (
    AssistantMessageEvent,
    DoneEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
)
from ..types import (
    AssistantMessage,
    ModelContext,
    ModelOptions,
    ModelSpec,
    TextContent,
)
class FakeProvider:
    id: str = "fake"
    name: str = "Fake Provider"

    def __init__(
            self,
            chunks: list[str],
            delay_seconds: float=0.2
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds must not be negative")

        self._chunks = chunks.copy()
        self._delay_seconds = delay_seconds

        self._model = ModelSpec(
            id="fake-text",
            name="Fake Text Model",
            provider=self.id,
            api="fake",
            base_url="fake://local",
        )

    def get_models(self) -> list[ModelSpec]:
        return [self._model]

    async def stream(
        self,
        model: ModelSpec,
        context: ModelContext,
        options: ModelOptions | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        if(
            model.provider != self.id
            or model.id !=self._model.id
            or model.api !=self._model.api
        ):
            raise ValueError("The request model is not supported")

        message = AssistantMessage(
            content=[],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=None,
            stop_reason="pending",
            timestamp=int(time.time()*1000),
        )
        yield StartEvent(partial=message)

        text_block = TextContent(text="")
        message.content.append(text_block)

        yield TextStartEvent(
            content_index=0,
            partial=message,
        )

        for chunk in self._chunks:
            await asyncio.sleep(self._delay_seconds)

            text_block.text += chunk

            yield TextDeltaEvent(
                content_index=0,
                delta=chunk,
                partial=message,
            )

        yield TextEndEvent(
            content_index=0,
            content=text_block.text,
            partial=message,
        )

        message.stop_reason = "stop"

        yield DoneEvent(
            reason="stop",
            message=message,
        ) 
        