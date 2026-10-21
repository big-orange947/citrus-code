from pydantic import JsonValue, TypeAdapter

from ..types import ToolCall
from .chat_stream_types import ChatToolCallDelta


ARGUMENTS_ADAPTER = TypeAdapter(dict[str, JsonValue])


class ToolCallBuffer:
    def __init__(
            self,
            tool_index: int,
            content_index: int
    ) -> None:
        self.tool_index = tool_index
        self.content_index = content_index
        self.arguments_text = ""

        self.tool_call = ToolCall(
            id="",
            name="",
            arguments={},
        )


    def append(self, delta: ChatToolCallDelta) -> str:
        if delta.index != self.tool_index:
            raise ValueError(
                "The delta belongs to a different tool call"
            )

        if delta.id is not None:
            if self.tool_call.id and self.tool_call.id !=delta.id:
                raise ValueError("The tool call ID changed")

            self.tool_call.id = delta.id

        if delta.function is None:
            return ""

        if delta.function.name is not None:
            name = delta.function.name

            if self.tool_call.name and self.tool_call.name != name:
                raise ValueError("The tool name changed")

            self.tool_call.name = name

        fragment = delta.function.arguments

        if fragment is None:
            return ""

        self.arguments_text += fragment

        return fragment


    def finish(self) -> ToolCall:
        if not self.tool_call.id:
            raise ValueError("The tool call ID is missing")

        if not self.tool_call.name:
            raise ValueError("The tool name is missing")

        arguments = ARGUMENTS_ADAPTER.validate_json(
            self.arguments_text
        )

        self.tool_call.arguments = arguments

        return self.tool_call