import asyncio
import sys
from contextlib import aclosing
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...agent.types import ToolExecutionResult
from ...ai.types import TextContent, ToolDefinition
from .local_search import LocalSearchOperations
from .operations import SearchMatch, SearchOperations
from .truncate import DEFAULT_MAX_BYTES, GREP_MAX_LINE_LENGTH, truncate_text


class GrepArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pattern: str = Field(description="Search pattern: regex, or a literal string when literal is true.")
    path: str = Field(default=".", description="File or directory to search.")
    glob: str | None = Field(default=None, description="Filter files, e.g. '*.py'.")
    ignore_case: bool = Field(default=False, alias="ignoreCase")
    literal: bool = False
    context: int = Field(default=0, ge=0, description="Context lines before and after each match.")
    limit: int = Field(default=100, ge=1, description="Maximum matching lines to return.")


class GrepTool:
    name: str = "grep"
    description: str = (
        "Search file contents using regex or literal text. Respects .gitignore, "
        "includes hidden files, and returns paths with line numbers. Default limit "
        "is 100 matching lines, with a 50KB output cap and 500 characters per line."
    )

    def __init__(self, cwd: Path, *, operations: SearchOperations | None = None) -> None:
        self._cwd = cwd
        self._ops = LocalSearchOperations() if operations is None else operations

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=GrepArguments.model_json_schema(by_alias=True),
        )

    async def execute(
        self, tool_call_id: str, arguments: dict[str, JsonValue]
    ) -> ToolExecutionResult:
        args = GrepArguments.model_validate(arguments)
        root = await asyncio.to_thread(self._ops.resolve_path, args.path, self._cwd)
        matches: list[SearchMatch] = []
        async with aclosing(self._ops.grep(
            args.pattern, root, cwd=self._cwd,
            glob=args.glob, ignore_case=args.ignore_case, literal=args.literal,
        )) as stream:
            async for match in stream:
                matches.append(match)
                if len(matches) >= args.limit:
                    break

        if not matches:
            return ToolExecutionResult(content=[TextContent(text="No matches found")])

        output: list[str] = []
        cache: dict[str, list[str]] = {}
        lines_truncated = False
        output_bytes = 0
        for match in matches:
            path, label, line_number = match.path, match.label, match.line_number
            if args.context:
                if path not in cache:
                    cache[path] = await asyncio.to_thread(self._ops.read_lines, path)
                lines = cache[path]
                start = max(1, line_number - args.context)
                end = min(len(lines), line_number + args.context)
            else:
                lines = [match.text.rstrip("\r\n")]
                start = end = line_number

            for number in range(start, end + 1):
                text = lines[number - 1] if args.context else lines[0]
                if len(text) > GREP_MAX_LINE_LENGTH:
                    text = text[:GREP_MAX_LINE_LENGTH] + "... [truncated]"
                    lines_truncated = True
                separator = ":" if number == line_number else "-"
                row = f"{label}{separator}{number}{separator} {text}"
                output.append(row)
                output_bytes += len(row.encode("utf-8")) + 1
                if output_bytes > DEFAULT_MAX_BYTES:
                    break
            if output_bytes > DEFAULT_MAX_BYTES:
                break

        truncation = truncate_text("\n".join(output), max_lines=sys.maxsize)
        text = truncation.content
        details: dict[str, JsonValue] = {}
        notices: list[str] = []
        if len(matches) >= args.limit:
            details["matchLimitReached"] = args.limit
            notices.append(f"{args.limit} matches limit reached. Refine the pattern or increase limit")
        if truncation.truncated:
            details["truncation"] = truncation.model_dump(mode="json", exclude={"content"})
            notices.append("50KB output limit reached")
        if lines_truncated:
            details["linesTruncated"] = True
            notices.append("Some lines were truncated to 500 characters. Use read for full lines")
        if notices:
            text += "\n\n[" + ". ".join(notices) + "]"
        return ToolExecutionResult(content=[TextContent(text=text)], details=details)
