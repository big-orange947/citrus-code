import os
import sys
from contextlib import aclosing
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...agent.types import ToolExecutionResult
from ...ai.types import TextContent, ToolDefinition
from .paths import resolve_to_cwd
from .processes import command_lines, find_executable
from .truncate import truncate_text


class FindArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pattern: str = Field(min_length=1, description="Glob pattern, e.g. '*.py' or 'src/**/*.py'.")
    path: str = Field(default=".", description="Directory to search.")
    limit: int = Field(default=1000, ge=1, description="Maximum results to return.")


class FindTool:
    name: str = "find"
    description: str = (
        "Find paths by glob pattern, respecting .gitignore and including hidden "
        "files. Results are relative to the search directory. Defaults to 1000 "
        "results, with a 50KB output cap."
    )

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd.resolve()

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=FindArguments.model_json_schema(),
        )

    async def execute(
        self, tool_call_id: str, arguments: dict[str, JsonValue]
    ) -> ToolExecutionResult:
        args = FindArguments.model_validate(arguments)
        root = resolve_to_cwd(args.path, self._cwd)
        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: {root}")
        command = [find_executable("fd"), "--glob", "--color=never", "--hidden", "--max-results", str(args.limit)]
        if not any((parent / ".git").exists() for parent in (root, *root.parents)):
            command.append("--no-require-git")
        pattern = args.pattern
        if "/" in pattern:
            command.append("--full-path")
            if not pattern.startswith(("/", "**/")) and pattern != "**":
                pattern = "**/" + pattern
            if os.name == "nt":
                pattern = pattern.replace("/", r"[/\\]")
        command.extend(["--", pattern, str(root)])
        results: list[str] = []
        async with aclosing(command_lines(command, self._cwd)) as stream:
            async for line in stream:
                trailing = line.endswith(("/", "\\"))
                path = Path(line)
                relative = path.relative_to(root) if path.is_absolute() else path
                label = relative.as_posix()
                if trailing and not label.endswith("/"):
                    label += "/"
                results.append(label)
                if len(results) >= args.limit:
                    break

        if not results:
            return ToolExecutionResult(content=[TextContent(text="No files found matching pattern")])
        truncation = truncate_text("\n".join(results), max_lines=sys.maxsize)
        text = truncation.content
        details: dict[str, JsonValue] = {}
        notices: list[str] = []
        if len(results) >= args.limit:
            details["resultLimitReached"] = args.limit
            notices.append(f"{args.limit} results limit reached. Refine the pattern or increase limit")
        if truncation.truncated:
            details["truncation"] = truncation.model_dump(mode="json", exclude={"content"})
            notices.append("50KB output limit reached")
        if notices:
            text += "\n\n[" + ". ".join(notices) + "]"
        return ToolExecutionResult(content=[TextContent(text=text)], details=details)
