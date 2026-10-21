from ..ai.types import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolResultMessage,
)


def get_pending_tool_calls(
    messages: list[Message],
) -> list[ToolCall]:
    """Check tool-message pairing and return unfinished calls."""
    pending: dict[str, ToolCall] = {}

    for message in messages:
        if isinstance(message, ToolResultMessage):
            call = pending.get(message.tool_call_id)

            if call is None:
                raise ValueError(
                    "Tool result has no matching pending call"
                )

            if call.name != message.tool_name:
                raise ValueError(
                    "Tool result name does not match the call"
                )

            del pending[message.tool_call_id]
            continue

        if pending:
            raise ValueError(
                "Tool results are missing before the next message"
            )

        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolCall):
                    if block.id in pending:
                        raise ValueError(
                            "Duplicate tool call ID in one message"
                        )

                    pending[block.id] = block

    return list(pending.values())