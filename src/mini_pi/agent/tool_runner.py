import time

from pydantic import JsonValue

from ..ai.types import (
    TextContent,
    ToolCall,
    ToolContent,
    ToolDefinition,
    ToolResultMessage,
)
from .hooks import AgentHooks
from .types import AgentTool, ToolRunResult


class ToolRunner:
    def __init__(
        self,
        tools: list[AgentTool],
    ) -> None:
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
                    "The tool definition name does not match "
                    f"the registered name: {tool.name}"
                )

            definitions.append(definition)

        return definitions

    async def execute(
        self,
        call: ToolCall,
        hooks: AgentHooks | None = None,
    ) -> ToolRunResult:
        block_reason: str | None = None

        # 1. 执行前，询问 Hook 是否允许这次调用。
        if hooks is not None:
            block_reason = await hooks.before_tool(
                call.model_copy(deep=True)
            )

            if (
                block_reason is not None
                and not isinstance(block_reason, str)
            ):
                raise TypeError(
                    "before_tool must return a string or None"
                )

        content: list[ToolContent]
        details: dict[str, JsonValue]
        is_error: bool

        # 2. 被拒绝时，不执行工具，直接准备失败结果。
        if block_reason is not None:
            content = [
                TextContent(
                    text=(
                        f"Tool '{call.name}' was blocked: "
                        f"{block_reason}"
                    )
                )
            ]

            details = {
                "blocked": True,
                "block_reason": block_reason,
            }

            is_error = True

        else:
            # 3. 允许执行，工具异常转成失败结果。
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
                            f"Tool '{call.name}' failed "
                            f"with {error_type}: {exc}"
                        )
                    )
                ]

                details = {
                    "error_type": error_type,
                }

                is_error = True

        # 4. 三种情况都包装成相同的结果类型。
        result = ToolRunResult(
            message=ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=content,
                is_error=is_error,
                timestamp=int(time.time() * 1000),
            ),
            details=details,
        )

        # 5. 结果进入历史之前，交给 Hook 处理。
        if hooks is not None:
            result = await hooks.after_tool(
                call.model_copy(deep=True),
                result.model_copy(deep=True),
            )

            if not isinstance(result, ToolRunResult):
                raise TypeError(
                    "after_tool must return a ToolRunResult"
                )

            if result.message.tool_call_id != call.id:
                raise ValueError(
                    "after_tool must preserve tool_call_id"
                )

            if result.message.tool_name != call.name:
                raise ValueError(
                    "after_tool must preserve tool_name"
                )

        return result
