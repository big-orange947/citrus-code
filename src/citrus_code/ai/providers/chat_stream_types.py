from pydantic import BaseModel, ConfigDict, Field, JsonValue
from typing import Literal


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

class ChatToolFunctionDelta(ApiModel):
    name: str | None = None
    arguments: str | None = None


class ChatToolCallDelta(ApiModel):
    index: int = Field(ge=0)
    id: str | None = None
    type: Literal["function"] | None = None
    function: ChatToolFunctionDelta | None = None


class ChatStreamDelta(ApiModel):
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ChatToolCallDelta] | None = None


class ChatStreamChoice(ApiModel):
    index: int = Field(ge=0)
    delta: ChatStreamDelta
    finish_reason: str | None = None


class ChatStreamUsage(ApiModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    prompt_cache_hit_tokens: int | None = Field(
        default=None,
        ge=0,
    )


class ChatStreamChunk(ApiModel):
    choices: list[ChatStreamChoice] = Field(
        default_factory=list,
    )
    usage: ChatStreamUsage | None = None
    error: JsonValue = None
