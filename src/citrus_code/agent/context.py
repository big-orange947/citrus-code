"""Application-supplied context preparation at a model-call boundary."""
from typing import Optional

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..ai.types import Message, ModelContext
from .cancellation import AbortSignal


@dataclass(frozen=True)
class PreparedContext:
    messages: list[Message]
    context: ModelContext
    tokens_before: int
    tokens_after: int


ContextPreparer = Callable[
    [ModelContext, AbortSignal], Awaitable[Optional[PreparedContext]]
]
