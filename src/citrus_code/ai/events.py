from typing import Annotated,Literal

from pydantic import BaseModel,ConfigDict,Field

from .types import AssistantMessage,ToolCall

class PartialEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partial: AssistantMessage


class StartEvent(PartialEvent):
    type: Literal["start"] = "start"
    

class TextStartEvent(PartialEvent):
    type: Literal["text_start"]="text_start"
    content_index: int = Field(ge=0)


class TextDeltaEvent(PartialEvent):
    type: Literal["text_delta"] = "text_delta"
    content_index: int = Field(ge=0)
    delta: str


class TextEndEvent(PartialEvent):
    type: Literal["text_end"] = "text_end"
    content_index: int = Field(ge=0)
    content: str


class ToolCallStartEvent(PartialEvent):
    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int = Field(ge=0)


class ToolCallDeltaEvent(PartialEvent):
    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int = Field(ge=0)
    delta: str


class ToolCallEndEvent(PartialEvent):
    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int = Field(ge=0)
    tool_call: ToolCall


class DoneEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["done"] = "done"
    reason: Literal["stop", "length", "toolUse"]
    message: AssistantMessage


class ErrorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["error"] = "error"
    reason: Literal["error", "aborted"]
    error: AssistantMessage
    retryable: bool = False
    retry_after_seconds: float | None = Field(default=None, ge=0)


class RetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["retry"] = "retry"
    attempt: int = Field(ge=1)
    max_retries: int = Field(ge=1)
    delay_seconds: float = Field(ge=0)
    error_message: str


AssistantMessageEvent = Annotated[
    StartEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | DoneEvent
    | RetryEvent
    | ErrorEvent,
    Field(discriminator="type"),
]
