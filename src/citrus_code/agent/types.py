from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ..ai.types import ToolContent, ToolDefinition, ToolResultMessage


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: list[ToolContent]
    details: dict[str, JsonValue] = Field(default_factory=dict)


class AgentTool(Protocol):
    name: str
    description: str

    def get_definition(self) -> ToolDefinition:
        ...

    async def execute(
            self,
            tool_call_id: str,
            arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult:
        ...


class ToolRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: ToolResultMessage
    details: dict[str, JsonValue] = Field(default_factory=dict)
