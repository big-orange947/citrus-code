"""Add the current skill catalog to model requests without changing session history."""
import time
from typing import Optional

from ..agent.hooks import AgentHooks
from ..agent.types import ToolRunResult
from ..ai.types import AssistantMessage, ModelContext, SystemMessage, TextContent, ToolCall
from .skills import SkillManager


class SkillHooks(AgentHooks):
    def __init__(self, delegate: AgentHooks, skills: SkillManager) -> None:
        self.delegate = delegate
        self.skills = skills

    async def before_model(self, context: ModelContext) -> ModelContext:
        context = await self.delegate.before_model(context)
        catalog = self.skills.catalog()
        mcp_tools = [tool.name for tool in context.tools if tool.name.startswith("mcp_")]
        if mcp_tools:
            catalog += "\n本轮另有 MCP 工具，具体名称与参数以工具声明为准：" + ", ".join(mcp_tools)
        if not catalog:
            return context
        context = context.model_copy(deep=True)
        for message in context.messages:
            if isinstance(message, SystemMessage):
                if isinstance(message.content, str):
                    message.content += "\n\n" + catalog
                else:
                    message.content.append(TextContent(text=catalog))
                return context
        context.messages.insert(0, SystemMessage(content=catalog, timestamp=int(time.time() * 1000)))
        return context

    async def after_model(self, message: AssistantMessage) -> None:
        await self.delegate.after_model(message)

    async def before_tool(self, call: ToolCall) -> Optional[str]:
        return await self.delegate.before_tool(call)

    async def after_tool(self, call: ToolCall, result: ToolRunResult) -> ToolRunResult:
        return await self.delegate.after_tool(call, result)
