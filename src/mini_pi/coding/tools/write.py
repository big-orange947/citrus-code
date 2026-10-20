import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...agent.types import ToolExecutionResult
from ...ai.types import TextContent, ToolDefinition
from .mutation import file_mutation
from .paths import resolve_to_cwd


class WriteArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    path: str = Field(
        min_length=1,
        description=(
            "The file path, absolute or relative "
            "to the working directory."
        ),
    )

    content: str = Field(
        description="The complete text content to write.",
    )


class WriteTool:
    name: str = "write"

    description: str = (
        "Write UTF-8 text to a file. "
        "Creates the file if it does not exist, "
        "overwrites it if it does, "
        "and automatically creates parent directories. "
        "Use this tool for new files or complete rewrites."
    )

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd.resolve()

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=WriteArguments.model_json_schema(),
        )

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult:
        args = WriteArguments.model_validate(arguments)

        path = resolve_to_cwd(args.path, self._cwd)

        return await asyncio.to_thread(
            self._write,
            path,
            args,
        )

    def _write(
        self,
        path: Path,
        args: WriteArguments,
    ) -> ToolExecutionResult:
        with file_mutation(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            characters_written = path.write_text(
                args.content,
                encoding="utf-8",
                newline="",
            )

        return ToolExecutionResult(
            content=[
                TextContent(
                    text=(
                        f"Successfully wrote "
                        f"{characters_written} characters "
                        f"to {args.path}."
                    )
                )
            ],
            details={
                "path": str(path),
                "characters_written": characters_written,
            },
        )
