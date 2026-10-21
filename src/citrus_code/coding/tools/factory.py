from collections.abc import Iterable
from pathlib import Path

from ...agent.types import AgentTool
from .bash import BashTool
from .edit import EditTool
from .find import FindTool
from .grep import GrepTool
from .ls import LsTool
from .local_files import LocalFileOperations
from .local_search import LocalSearchOperations
from .local_shell import LocalShellOperations
from .operations import ToolOperations
from .powershell import PowerShellTool
from .read import ReadTool
from .write import WriteTool


DEFAULT_CODING_TOOLS = ("read", "bash", "edit", "write")
READ_ONLY_TOOLS = ("read", "grep", "find", "ls")

ALL_TOOL_NAMES = ("read", "bash", "powershell", "edit", "write", "grep", "find", "ls")


def create_local_operations() -> ToolOperations:
    files = LocalFileOperations()
    return ToolOperations(files=files, shell=LocalShellOperations(), search=LocalSearchOperations(files))


def create_tool(name: str, cwd: Path, *, operations: ToolOperations | None = None) -> AgentTool:
    if name not in ALL_TOOL_NAMES:
        raise ValueError(f"Unknown built-in tool: {name}")
    ops = create_local_operations() if operations is None else operations
    if name == "read":
        return ReadTool(cwd, operations=ops.files)
    if name == "bash":
        return BashTool(cwd, operations=ops.shell)
    if name == "powershell":
        return PowerShellTool(cwd, operations=ops.shell)
    if name == "edit":
        return EditTool(cwd, operations=ops.files)
    if name == "write":
        return WriteTool(cwd, operations=ops.files)
    if name == "grep":
        return GrepTool(cwd, operations=ops.search)
    if name == "find":
        return FindTool(cwd, operations=ops.search)
    return LsTool(cwd, operations=ops.files)


def create_tools(
    cwd: Path, names: Iterable[str], *, operations: ToolOperations | None = None,
) -> list[AgentTool]:
    ops = create_local_operations() if operations is None else operations
    tools: list[AgentTool] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ValueError(f"Duplicate built-in tool: {name}")
        seen.add(name)
        tools.append(create_tool(name, cwd, operations=ops))
    return tools


def create_coding_tools(cwd: Path, *, operations: ToolOperations | None = None) -> list[AgentTool]:
    return create_tools(cwd, DEFAULT_CODING_TOOLS, operations=operations)


def create_read_only_tools(cwd: Path, *, operations: ToolOperations | None = None) -> list[AgentTool]:
    return create_tools(cwd, READ_ONLY_TOOLS, operations=operations)


def create_all_tools(cwd: Path, *, operations: ToolOperations | None = None) -> dict[str, AgentTool]:
    ops = create_local_operations() if operations is None else operations
    return {name: create_tool(name, cwd, operations=ops) for name in ALL_TOOL_NAMES}
