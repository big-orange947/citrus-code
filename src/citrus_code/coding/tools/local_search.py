"""Default search operations using host ripgrep/fd and file operations."""
from typing import Optional

import base64
from collections.abc import AsyncGenerator
from ...compat import aclosing
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .local_files import LocalFileOperations
from .operations import FileOperations, SearchMatch
from .processes import command_lines, find_executable


class _RgText(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: Optional[str] = None
    bytes: Optional[str] = None

    def value(self) -> str:
        if self.text is not None:
            return self.text
        if self.bytes is not None:
            return os.fsdecode(base64.b64decode(self.bytes))
        raise ValueError("ripgrep returned an empty text field")


class _RgData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: Optional[_RgText] = None
    lines: Optional[_RgText] = None
    line_number: Optional[int] = None


class _RgEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    data: Optional[_RgData] = None


class LocalSearchOperations:
    """Host search programs; changing only files does not sandbox these programs."""

    def __init__(self, files: Optional[FileOperations] = None) -> None:
        self._files = LocalFileOperations() if files is None else files

    def resolve_path(self, value: str, cwd: Path) -> str:
        return self._files.resolve_path(value, cwd)

    def read_lines(self, path: str) -> list[str]:
        return self._files.read_bytes(path).decode("utf-8", errors="replace").splitlines()

    async def find(self, pattern: str, root: str, *, cwd: Path, limit: int) -> AsyncGenerator[str, None]:
        directory = Path(root)
        if not directory.is_dir():
            raise NotADirectoryError(f"Not a directory: {root}")
        command = [find_executable("fd"), "--glob", "--color=never", "--hidden", "--max-results", str(limit)]
        if not any((parent / ".git").exists() for parent in (directory, *directory.parents)):
            command.append("--no-require-git")
        if "/" in pattern:
            command.append("--full-path")
            if not pattern.startswith(("/", "**/")) and pattern != "**":
                pattern = "**/" + pattern
            if os.name == "nt":
                pattern = pattern.replace("/", r"[/\\]")
        command.extend(["--", pattern, root])
        async with aclosing(command_lines(command, cwd)) as stream:
            async for line in stream:
                trailing = line.endswith(("/", "\\"))
                path = Path(line)
                relative = path.relative_to(directory) if path.is_absolute() else path
                label = relative.as_posix()
                if trailing and not label.endswith("/"):
                    label += "/"
                yield label

    async def grep(
        self,
        pattern: str,
        root: str,
        *,
        cwd: Path,
        glob: Optional[str],
        ignore_case: bool,
        literal: bool,
    ) -> AsyncGenerator[SearchMatch, None]:
        search_root = Path(root)
        if not search_root.exists():
            raise FileNotFoundError(f"Path not found: {root}")
        root_is_directory = search_root.is_dir()
        command = [find_executable("rg"), "--json", "--line-number", "--color=never", "--hidden", "--sort=path"]
        if ignore_case:
            command.append("--ignore-case")
        if literal:
            command.append("--fixed-strings")
        if glob is not None:
            command.extend(["--glob", glob])
        command.extend(["--", pattern, root])
        async with aclosing(command_lines(command, cwd, success_codes=(0, 1))) as stream:
            async for raw in stream:
                event = _RgEvent.model_validate_json(raw)
                if event.type != "match":
                    continue
                data = event.data
                if data is None or data.path is None or data.lines is None or data.line_number is None:
                    raise ValueError("ripgrep returned an invalid match")
                path = Path(data.path.value())
                label = path.relative_to(search_root).as_posix() if root_is_directory else path.name
                yield SearchMatch(str(path), label, data.line_number, data.lines.value())
