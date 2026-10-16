import time

from pydantic import JsonValue

from ..ai.types import (
    TextContent,
    ToolCall,
    ToolDefinition,
    ToolResultMessage,
)
from .types import AgentTool, ToolRunResult


class ToolRunner:
    def __init__(self,tools: list[AgentTool]) -> None:
        self._tools: dict[str, AgentTool] = {}

        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(
                    f"Tool already registered: {tool.name}"
                )

            self._tools[tool.name] = tool



    def get_definitions(self) -> list[ToolDefinition]:
        definitions: list[ToolDefinition] = []

        for tool in self._tools.values():
            definition = tool.get_definition()

            if definition.name != tool.name:
                raise ValueError(
                    "The tool definition name does not match"
                    f"the registered name:{tool.name}"
                )

            definitions.append(definition)

        return definitions


    async def execute(self, call: ToolCall) -> ToolRunResult:
        details: dict[str, JsonValue] = {}

        try:
            tool = self._tools.get(call.name)

            if tool is None:
                raise ValueError(
                    f"Unknown tool: {call.name}"
                )

            execution = await tool.execute(
                tool_call_id=call.id,
                arguments=call.arguments,
            )

            content = execution.content
            details = execution.details
            is_error = False

        except Exception as exc:
            error_type = type(exc).__name__

            content = [
                TextContent(
                    text=(
                        f"Tool '{call.name}' failed"
                        f"with {error_type}: {exc}"
                    )
                )
            ]

            details = {
                "error_type": error_type,
            }

            is_error = True

        message = ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=content,
            is_error=is_error,
            timestamp=int(time.time()*1000),
        )

        return ToolRunResult(
            message=message,
            details=details,
        )
        