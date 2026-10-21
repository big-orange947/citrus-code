"""Execution contracts, independent of the model-facing tool schemas."""

from collections.abc import AsyncGenerator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, Protocol


ShellName = Literal["bash", "powershell"]


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    is_directory: bool


@dataclass(frozen=True)
class ShellResult:
    exit_code: int
    shell: str
    timed_out: bool = False


@dataclass(frozen=True)
class SearchMatch:
    path: str
    label: str
    line_number: int
    text: str


class FileOperations(Protocol):
    """Synchronous file operations; tools invoke them in worker threads.

    Paths are opaque backend strings, not necessarily host paths. Resolve them
    in the same environment used for reads, writes and directory listings.
    """

    def resolve_path(self, value: str, cwd: Path) -> str: ...

    def read_bytes(self, path: str) -> bytes: ...

    def write_bytes(self, path: str, content: bytes) -> None: ...

    def mkdir_parent(self, path: str) -> None: ...

    def list_directory(self, path: str) -> list[DirectoryEntry]: ...

    def mutation(self, path: str) -> AbstractContextManager[None]:
        """Hold a per-file lock across the whole read/modify/write operation."""
        ...


class ShellOperations(Protocol):
    async def execute(
        self,
        command: str,
        cwd: Path,
        *,
        shell: ShellName,
        shell_path: str | None,
        timeout: float | None,
        output: BinaryIO,
    ) -> ShellResult:
        """Write output to the sink; clean up execution on timeout/cancellation."""
        ...

    def persist_output(self, local_path: Path, cwd: Path) -> str:
        """Publish a truncated log where this backend's file tools can read it.

        Called in a worker thread. Return the local path to retain that file,
        or copy/upload it and return a backend path so the local copy can go.
        """
        ...


class SearchOperations(Protocol):
    def resolve_path(self, value: str, cwd: Path) -> str: ...

    def read_lines(self, path: str) -> list[str]:
        """Read grep context in the search environment, from a worker thread."""
        ...

    def find(self, pattern: str, root: str, *, cwd: Path, limit: int) -> AsyncGenerator[str, None]:
        """Yield relative labels; closing the generator must stop the search."""
        ...

    def grep(
        self,
        pattern: str,
        root: str,
        *,
        cwd: Path,
        glob: str | None,
        ignore_case: bool,
        literal: bool,
    ) -> AsyncGenerator[SearchMatch, None]:
        """Yield matches; paths and labels belong to the backend environment."""
        ...


@dataclass(frozen=True)
class ToolOperations:
    """Supply all three explicitly when replacing the complete tool backend."""

    files: FileOperations
    shell: ShellOperations
    search: SearchOperations
