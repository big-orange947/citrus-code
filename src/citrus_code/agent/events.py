from typing import Optional, Union
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..ai.events import AssistantMessageEvent
from ..ai.types import (
    AssistantMessage,
    Message,
    ToolCall,
)
from .types import ToolRunResult


class AgentEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentStartEvent(AgentEventBase):
    type: Literal["agent_start"] = "agent_start"


class TurnStartEvent(AgentEventBase):
    type: Literal["turn_start"] = "turn_start"
    turn: int = Field(ge=1)


class MessageAcceptedEvent(AgentEventBase):
    type: Literal["message_accepted"] = "message_accepted"

    message: Message
    source: Literal["steering", "follow_up"]


class ModelEvent(AgentEventBase):
    type: Literal["model_event"] = "model_event"
    event: AssistantMessageEvent


class AssistantMessageCommittedEvent(AgentEventBase):
    type: Literal["assistant_message_committed"] = (
        "assistant_message_committed"
    )

    message: AssistantMessage


class ToolExecutionStartEvent(AgentEventBase):
    type: Literal["tool_execution_start"] = (
        "tool_execution_start"
    )

    call: ToolCall


class ToolExecutionEndEvent(AgentEventBase):
    type: Literal["tool_execution_end"] = (
        "tool_execution_end"
    )

    call: ToolCall
    result: ToolRunResult


class ContextCompactedEvent(AgentEventBase):
    type: Literal["context_compacted"] = "context_compacted"
    messages: list[Message]
    tokens_before: int = Field(ge=0)
    tokens_after: int = Field(ge=0)


class AgentEndEvent(AgentEventBase):
    type: Literal["agent_end"] = "agent_end"

    reason: Literal[
        "stop",
        "length",
        "error",
        "aborted",
        "max_turns",
    ]

    messages: list[Message]
    error_message: Optional[str] = None


AgentEvent = Annotated[
    Union[
        AgentStartEvent,
        TurnStartEvent,
        MessageAcceptedEvent,
        ModelEvent,
        AssistantMessageCommittedEvent,
        ToolExecutionStartEvent,
        ToolExecutionEndEvent,
        ContextCompactedEvent,
        AgentEndEvent,
    ],
    Field(discriminator="type"),
]
