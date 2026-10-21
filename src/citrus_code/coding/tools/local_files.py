"""Default file operations using the host filesystem."""

from contextlib import AbstractContextManager
from pathlib import Path

from .mutation import file_mutation
from .operations import DirectoryEntry
from .paths import resolve_to_cwd


class LocalFileOperations:
    def resolve_path(self, value: str, cwd: Path) -> str:
        return str(resolve_to_cwd(value, cwd))

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def write_bytes(self, path: str, content: bytes) -> None:
        Path(path).write_bytes(content)

    def mkdir_parent(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    def list_directory(self, path: str) -> list[DirectoryEntry]:
        directory = Path(path)
        if not directory.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")
        return [DirectoryEntry(entry.name, entry.is_dir()) for entry in directory.iterdir()]

    def mutation(self, path: str) -> AbstractContextManager[None]:
        return file_mutation(Path(path))
