from collections.abc import AsyncIterator
from typing import Protocol

from .events import AssistantMessageEvent
from .types import ModelContext, ModelOptions, ModelSpec


class Provider(Protocol):
    id: str
    name: str

    def get_models(self) -> list[ModelSpec]:
        ...
    def stream(
            self,
            model: ModelSpec,
            context: ModelContext,
            options: ModelOptions | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        ...
