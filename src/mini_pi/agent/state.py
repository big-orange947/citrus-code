from pydantic import BaseModel, ConfigDict, Field

from ..ai.types import AssistantMessage, Message


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[Message] = Field(default_factory=list)
    is_running: bool = False
    turn: int = Field(default=0, ge=0)
    current_response: AssistantMessage | None = None
    error_message: str | None = None