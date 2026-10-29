from typing import Optional, Union
from typing import Annotated,Literal

from pydantic import BaseModel,ConfigDict,Field,JsonValue


class TextContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str


class ImageContent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["image"] = "image"
    data: str
    mime_type: str = Field(alias="mimeType")


ToolContent = Annotated[
    Union[TextContent, ImageContent],
    Field(discriminator="type"),
]


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["toolCall"]="toolCall"
    id: str
    name: str
    arguments: dict[str,JsonValue]

class SystemMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system"] = "system"
    content: Union[str, list[TextContent]]
    timestamp: int 


class UserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user"] = "user"
    content: Union[str, list[ToolContent]]
    timestamp: int


class ToolResultMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str
    tool_name: str
    content: list[ToolContent]
    is_error: bool
    timestamp: int

StopReason = Literal[
    "pending",
    "stop",
    "length",
    "toolUse",
    "error",
    "aborted",
]


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    cache_read_tokens: Optional[int] = Field(default=None,ge=0)
    cache_write_tokens: Optional[int] = Field(default=None,ge=0)


AssistantContent = Annotated[
    Union[TextContent, ToolCall],
    Field(discriminator="type"),
]


class AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: list[AssistantContent]

    api: str
    provider: str
    model: str

    usage: Optional[Usage]
    stop_reason: StopReason
    error_message: Optional[str] = None
    reasoning_content: Optional[str] = None
    timestamp: int


Message = Annotated[
    Union[SystemMessage, UserMessage, AssistantMessage, ToolResultMessage],
    Field(discriminator="role"),
]


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    provider: str
    api: str
    base_url: str

    context_window: Optional[int] = Field(default=None,gt=0)
    max_output_tokens: Optional[int] = Field(default=None,gt=0)
    supports_images: bool = False


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: dict[str,JsonValue]


class ModelContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[Message]
    tools: list[ToolDefinition] = Field(default_factory=list)


class ModelOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_output_tokens: Optional[int] = Field(default=None,gt=0)
    temperature: Optional[float] = None
