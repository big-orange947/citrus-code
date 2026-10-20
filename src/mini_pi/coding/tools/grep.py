import asyncio
import base64
import os
import sys
from contextlib import aclosing
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...agent.types import ToolExecutionResult
from ...ai.types import TextContent, ToolDefinition
from .paths import resolve_to_cwd
from .processes import command_lines, find_executable
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


class _RgText(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str | None = None
    bytes: str | None = None

    def value(self) -> str:
        if self.text is not None:
            return self.text
        if self.bytes is not None:
            return os.fsdecode(base64.b64decode(self.bytes))
        raise ValueError("ripgrep returned an empty text field")


class _RgData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: _RgText | None = None
    lines: _RgText | None = None
    line_number: int | None = None


class _RgEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    data: _RgData | None = None


class GrepTool:
    name: str = "grep"
    description: str = (
        "Search file contents using regex or literal text. Respects .gitignore, "
        "includes hidden files, and returns paths with line numbers. Default limit "
        "is 100 matching lines, with a 50KB output cap and 500 characters per line."
    )

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd.resolve()

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
        root = resolve_to_cwd(args.path, self._cwd)
        if not root.exists():
            raise FileNotFoundError(f"Path not found: {root}")
        command = [find_executable("rg"), "--json", "--line-number", "--color=never", "--hidden", "--sort=path"]
        if args.ignore_case:
            command.append("--ignore-case")
        if args.literal:
            command.append("--fixed-strings")
        if args.glob is not None:
            command.extend(["--glob", args.glob])
        command.extend(["--", args.pattern, str(root)])

        matches: list[tuple[Path, int, str]] = []
        async with aclosing(command_lines(command, self._cwd, success_codes=(0, 1))) as stream:
            async for raw in stream:
                event = _RgEvent.model_validate_json(raw)
                if event.type != "match":
                    continue
                data = event.data
                if data is None or data.path is None or data.lines is None or data.line_number is None:
                    raise ValueError("ripgrep returned an invalid match")
                matches.append((Path(data.path.value()), data.line_number, data.lines.value()))
                if len(matches) >= args.limit:
                    break

        if not matches:
            return ToolExecutionResult(content=[TextContent(text="No matches found")])

        output: list[str] = []
        cache: dict[Path, list[str]] = {}
        lines_truncated = False
        output_bytes = 0
        for path, line_number, match_text in matches:
            label = path.relative_to(root).as_posix() if root.is_dir() else path.name
            if args.context:
                if path not in cache:
                    text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
                    cache[path] = text.splitlines()
                lines = cache[path]
                start = max(1, line_number - args.context)
                end = min(len(lines), line_number + args.context)
            else:
                lines = [match_text.rstrip("\r\n")]
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
