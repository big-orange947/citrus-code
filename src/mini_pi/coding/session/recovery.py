"""Repair missing results at a valid session tail without executing tools."""

import time

from ...ai.types import TextContent, ToolResultMessage
from ..history import get_pending_tool_calls
from .manager import SessionManager


def recover_pending_tools(manager: SessionManager) -> int:
    """Call before starting an Agent, with exclusive session ownership."""
    pending = get_pending_tool_calls(manager.build_context().messages)
    for call in pending:
        manager.append_message(
            ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=[
                    TextContent(
                        text=(
                            "The session was interrupted before a result was recorded "
                            "for this tool call. Its execution status is unknown: it "
                            "may not have started, may have partially run, or may have "
                            "completed. Inspect the actual state before deciding "
                            "whether to retry."
                        )
                    )
                ],
                is_error=True,
                timestamp=int(time.time() * 1000),
            )
        )
    return len(pending)
