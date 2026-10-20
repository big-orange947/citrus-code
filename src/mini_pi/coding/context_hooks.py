import time

from ..agent.hooks import AgentHooks
from ..ai.types import (
    Message,
    ModelContext,
    SystemMessage,
)


class SystemPromptHooks(AgentHooks):
    def __init__(
        self,
        system_prompt: str,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError(
                "The system prompt must not be empty"
            )

        self._system_prompt = system_prompt

    async def before_model(
            self,
            context: ModelContext,
    ) -> ModelContext:
        messages: list[Message] = [
            SystemMessage(
                content=self._system_prompt,
                timestamp=int(time.time() * 1000),
            )
        ]

        for message in context.messages:
            if not isinstance(message, SystemMessage):
                messages.append(message)

        return ModelContext(
            messages=messages,
            tools=context.tools,
        )
