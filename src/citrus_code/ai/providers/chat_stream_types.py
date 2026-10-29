from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from typing import Literal


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

class ChatToolFunctionDelta(ApiModel):
    name: Optional[str] = None
    arguments: Optional[str] = None


class ChatToolCallDelta(ApiModel):
    index: int = Field(ge=0)
    id: Optional[str] = None
    type: Optional[Literal['function']] = None
    function: Optional[ChatToolFunctionDelta] = None


class ChatStreamDelta(ApiModel):
    content: Optional[str] = None
    reasoning_content: Optional[str] = None
    tool_calls: Optional[list[ChatToolCallDelta]] = None


class ChatStreamChoice(ApiModel):
    index: int = Field(ge=0)
    delta: ChatStreamDelta
    finish_reason: Optional[str] = None


class ChatPromptTokensDetails(ApiModel):
    cached_tokens: Optional[int] = Field(default=None, ge=0)


class ChatStreamUsage(ApiModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    prompt_cache_hit_tokens: Optional[int] = Field(
        default=None,
        ge=0,
    )
    prompt_tokens_details: Optional[ChatPromptTokensDetails] = None


class ChatStreamChunk(ApiModel):
    choices: list[ChatStreamChoice] = Field(
        default_factory=list,
    )
    usage: Optional[ChatStreamUsage] = None
    error: JsonValue = None
