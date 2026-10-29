from typing import Optional
import asyncio
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...agent.types import ToolExecutionResult
from ...ai.types import TextContent, ToolDefinition
from .local_files import LocalFileOperations
from .operations import FileOperations
from .truncate import truncate_text


class LsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(default=".", description="Directory to list.")
    limit: int = Field(default=500, ge=1, description="Maximum entries to return.")


class LsTool:
    name: str = "ls"
    description: str = (
        "List directory entries alphabetically, including dotfiles. Directories "
        "have a '/' suffix. Defaults to 500 entries, with a 50KB output cap."
    )

    def __init__(self, cwd: Path, *, operations: Optional[FileOperations] = None) -> None:
        self._cwd = cwd
        self._ops = LocalFileOperations() if operations is None else operations

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=LsArguments.model_json_schema(),
        )

    async def execute(
        self, tool_call_id: str, arguments: dict[str, JsonValue]
    ) -> ToolExecutionResult:
        args = LsArguments.model_validate(arguments)
        path = await asyncio.to_thread(self._ops.resolve_path, args.path, self._cwd)
        return await asyncio.to_thread(self._list, path, args.limit)

    def _list(self, path: str, limit: int) -> ToolExecutionResult:
        entries = sorted(self._ops.list_directory(path), key=lambda entry: (entry.name.casefold(), entry.name))
        labels = [entry.name + ("/" if entry.is_directory else "") for entry in entries[:limit]]
        truncation = truncate_text("\n".join(labels), max_lines=sys.maxsize)
        text = truncation.content or "(empty directory)"
        details: dict[str, JsonValue] = {}
        notices: list[str] = []
        if len(entries) > limit:
            details["entryLimitReached"] = limit
            notices.append(f"{limit} entries limit reached. Increase limit for more")
        if truncation.truncated:
            details["truncation"] = truncation.model_dump(mode="json", exclude={"content"})
            notices.append("50KB output limit reached")
        if notices:
            text += "\n\n[" + ". ".join(notices) + "]"
        return ToolExecutionResult(content=[TextContent(text=text)], details=details)
