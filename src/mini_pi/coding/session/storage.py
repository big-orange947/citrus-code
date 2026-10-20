"""Synchronous JSONL persistence, intended for one writer per session file."""

import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from pydantic import TypeAdapter, ValidationError

from .types import (
    SessionFileEntry,
    SessionHeader,
    SessionLogEntry,
)


_RECORD_ADAPTER = TypeAdapter(SessionFileEntry)
_IO_LOCK = RLock()


class SessionFormatError(ValueError):
    def __init__(self, path: Path, line: int, reason: str) -> None:
        super().__init__(f"Invalid session file {path}, line {line}: {reason}")


class SessionConflictError(RuntimeError):
    """The stored file changed after this manager loaded or wrote it."""


@dataclass(frozen=True)
class _Revision:
    device: int
    inode: int
    size: int
    modified_ns: int

    @classmethod
    def from_stat(cls, stat: os.stat_result) -> "_Revision":
        return cls(stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


class JsonlSessionStore:
    def __init__(self, path: Path, revision: _Revision) -> None:
        self.path = path
        self._revision = revision
        self._failed = False

    @classmethod
    def create(
        cls,
        path: Path,
        header: SessionHeader,
        records: list[SessionLogEntry],
    ) -> "JsonlSessionStore":
        path = path.expanduser().resolve()
        with _IO_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Never overwrite an existing session, including a damaged file.
            with path.open("xb") as file:
                file.write(header.model_dump_json(by_alias=True).encode("utf-8") + b"\n")
                for record in records:
                    file.write(record.model_dump_json(by_alias=True).encode("utf-8") + b"\n")
                file.flush()
                os.fsync(file.fileno())
                revision = _Revision.from_stat(os.fstat(file.fileno()))
        return cls(path, revision)

    @classmethod
    def load(
        cls,
        path: Path,
    ) -> tuple["JsonlSessionStore", SessionHeader, list[tuple[int, SessionLogEntry]]]:
        path = path.expanduser().resolve()
        header: SessionHeader | None = None
        records: list[tuple[int, SessionLogEntry]] = []

        with _IO_LOCK:
            with path.open("rb") as file:
                before = _Revision.from_stat(os.fstat(file.fileno()))
                for line_number, line in enumerate(file, start=1):
                    if not line.strip():
                        continue
                    try:
                        record = _RECORD_ADAPTER.validate_json(line)
                    except ValidationError as exc:
                        raise SessionFormatError(
                            path, line_number, "invalid JSON or record structure"
                        ) from exc

                    if header is None:
                        if not isinstance(record, SessionHeader):
                            raise SessionFormatError(path, line_number, "the first record must be a session header")
                        header = record
                    elif isinstance(record, SessionHeader):
                        raise SessionFormatError(path, line_number, "duplicate session header")
                    else:
                        records.append((line_number, record))

                after = _Revision.from_stat(os.fstat(file.fileno()))
            if before != after or after != _Revision.from_stat(path.stat()):
                raise SessionConflictError(f"Session changed while loading: {path}")

        if header is None:
            raise SessionFormatError(path, 1, "missing session header")
        return cls(path, after), header, records

    def append(self, record: SessionLogEntry) -> None:
        payload = record.model_dump_json(by_alias=True).encode("utf-8") + b"\n"
        with _IO_LOCK:
            if self._failed:
                raise SessionConflictError(
                    "A previous write failed. Reload and inspect the session before writing again."
                )
            with self.path.open("r+b") as file:
                current = _Revision.from_stat(os.fstat(file.fileno()))
                if current != self._revision:
                    raise SessionConflictError(f"Session was changed by another writer: {self.path}")
                if current != _Revision.from_stat(self.path.stat()):
                    raise SessionConflictError(f"Session path was replaced: {self.path}")

                # Accept a valid final JSON record without LF, adding its separator
                # before the new record. An incomplete record was rejected on load.
                if current.size:
                    file.seek(-1, os.SEEK_END)
                    if file.read(1) != b"\n":
                        payload = b"\n" + payload
                file.seek(0, os.SEEK_END)
                try:
                    written = file.write(payload)
                    if written != len(payload):
                        raise OSError("Incomplete session write")
                    file.flush()
                    os.fsync(file.fileno())
                    self._revision = _Revision.from_stat(os.fstat(file.fileno()))
                except BaseException:
                    # A partial tail is not silently truncated or skipped.
                    self._failed = True
                    raise
