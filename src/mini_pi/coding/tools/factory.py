from collections.abc import Callable, Iterable
from pathlib import Path

from ...agent.types import AgentTool
from .bash import BashTool
from .edit import EditTool
from .find import FindTool
from .grep import GrepTool
from .ls import LsTool
from .powershell import PowerShellTool
from .read import ReadTool
from .write import WriteTool


DEFAULT_CODING_TOOLS = ("read", "bash", "edit", "write")
READ_ONLY_TOOLS = ("read", "grep", "find", "ls")

_FACTORIES: dict[str, Callable[[Path], AgentTool]] = {
    "read": ReadTool,
    "bash": BashTool,
    "powershell": PowerShellTool,
    "edit": EditTool,
    "write": WriteTool,
    "grep": GrepTool,
    "find": FindTool,
    "ls": LsTool,
}


def create_tool(name: str, cwd: Path) -> AgentTool:
    factory = _FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown built-in tool: {name}")
    return factory(cwd)


def create_tools(cwd: Path, names: Iterable[str]) -> list[AgentTool]:
    tools: list[AgentTool] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ValueError(f"Duplicate built-in tool: {name}")
        seen.add(name)
        tools.append(create_tool(name, cwd))
    return tools


def create_coding_tools(cwd: Path) -> list[AgentTool]:
    return create_tools(cwd, DEFAULT_CODING_TOOLS)


def create_read_only_tools(cwd: Path) -> list[AgentTool]:
    return create_tools(cwd, READ_ONLY_TOOLS)


def create_all_tools(cwd: Path) -> dict[str, AgentTool]:
    return {name: factory(cwd) for name, factory in _FACTORIES.items()}
