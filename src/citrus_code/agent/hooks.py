from typing import Optional
from ..ai.types import (
    AssistantMessage,
    ModelContext,
    ToolCall,
)
from .types import ToolRunResult


class AgentHooks:
    async def before_model(
        self,
        context: ModelContext,
    ) -> ModelContext:
        """Prepare the context for one model request."""
        return context

    async def after_model(
        self,
        message: AssistantMessage,
    ) -> None:
        """Observe the final response of one model request."""
        pass

    async def before_tool(
        self,
        call: ToolCall,
    ) -> Optional[str]:
        """Return a reason to block execution, or None to allow it."""
        return None

    async def after_tool(
        self,
        call: ToolCall,
        result: ToolRunResult,
    ) -> ToolRunResult:
        """Process a tool result before adding it to the history."""
        return result