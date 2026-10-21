import asyncio
import sys
from contextlib import aclosing
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...agent.types import ToolExecutionResult
from ...ai.types import TextContent, ToolDefinition
from .local_search import LocalSearchOperations
from .operations import SearchOperations
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

    def __init__(self, cwd: Path, *, operations: SearchOperations | None = None) -> None:
        self._cwd = cwd
        self._ops = LocalSearchOperations() if operations is None else operations

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
        root = await asyncio.to_thread(self._ops.resolve_path, args.path, self._cwd)
        results: list[str] = []
        async with aclosing(self._ops.find(args.pattern, root, cwd=self._cwd, limit=args.limit)) as stream:
            async for label in stream:
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
