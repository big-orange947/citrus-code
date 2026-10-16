import asyncio
from itertools import islice
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...agent.types import ToolExecutionResult
from ...ai.types import TextContent, ToolDefinition

class ReadArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True
    )
    path: str = Field(
        min_length=1,
        description="The file path, absolute or ralative to the working directory.",
    )

    offset: int = Field(
        default=1,
        ge=1,
        description="The first line to read, starting from 1.",
    )

    limit: int = Field(
        default=200,
        ge=1,
        le=2000,
        description="The maximum number of lines to read.",
    )


class ReadTool:
    name: str = "read"
    description: str = (
        "Read a UTF-8 text file. "
        "Use offset and limit to read a range of lines."
    )

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd.resolve()

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=ReadArguments.model_json_schema(),
        )

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult:
        args = ReadArguments.model_validate(arguments)

        path = Path(args.path)

        if not path.is_absolute():
            path = self._cwd / path

        path = path.resolve()

        return await asyncio.to_thread(
            self._read,
            path,
            args,
        )

    def _read(
        self,
        path: Path,
        args: ReadArguments,
    ) -> ToolExecutionResult:
        start = args.offset - 1
        stop = start + args.limit

        with path.open("r", encoding="utf-8") as file:
            lines = list(islice(file, start, stop))
            has_more = next(file, None) is not None

        text = "".join(lines)

        if not lines:
            text = "[No lines in the requested range.]"

        if has_more:
            next_offset = args.offset + len(lines)
            text += (
                "\n\n[More lines are available. "
                f"Use offset={next_offset} to continue.]"
            )

        return ToolExecutionResult(
            content=[TextContent(text=text)],
            details={
                "path": str(path),
                "offset": args.offset,
                "lines_read": len(lines),
                "has_more": has_more,
            },
        )