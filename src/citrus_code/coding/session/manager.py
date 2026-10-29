"""Session tree and context reconstruction with optional JSONL persistence."""
from typing import Optional

import time
from pathlib import Path
from threading import RLock
from uuid import uuid4

from ...ai.types import Message, UserMessage
from .projection import project_context_entries
from .storage import JsonlSessionStore, SessionFormatError
from .types import (
    ModelChangeEntry,
    CompactionEntry,
    ContextMessageEntry,
    SessionContext,
    SessionCursorEntry,
    SessionEntry,
    SessionHeader,
    SessionLogEntry,
    SessionMessageEntry,
)


class SessionManager:
    def __init__(self, cwd: Path) -> None:
        self._header = SessionHeader(
            id=uuid4().hex,
            timestamp=int(time.time() * 1000),
            cwd=str(cwd.expanduser().resolve()),
        )
        self._entries: list[SessionEntry] = []
        self._records: list[SessionLogEntry] = []
        self._by_id: dict[str, SessionEntry] = {}
        self._leaf_id: Optional[str] = None
        self._store: Optional[JsonlSessionStore] = None
        self._lock = RLock()

    @classmethod
    def create(cls, cwd: Path, file_path: Path) -> "SessionManager":
        session = cls(cwd)
        session.save(file_path)
        return session

    @classmethod
    def load(cls, file_path: Path) -> "SessionManager":
        store, header, records = JsonlSessionStore.load(file_path)
        session = cls(Path(header.cwd))
        session._header = header.model_copy(deep=True)

        # Replay without attaching the store: reading must never append records.
        for line_number, record in records:
            try:
                if isinstance(record, SessionCursorEntry):
                    if record.entry_id is not None and record.entry_id not in session._by_id:
                        raise ValueError(f"Unknown cursor target: {record.entry_id}")
                    session._leaf_id = record.entry_id
                    session._records.append(record.model_copy(deep=True))
                else:
                    session._append_entry(record)
            except ValueError as exc:
                raise SessionFormatError(store.path, line_number, str(exc)) from exc

        session._store = store
        return session

    @property
    def header(self) -> SessionHeader:
        with self._lock:
            return self._header.model_copy(deep=True)

    @property
    def leaf_id(self) -> Optional[str]:
        with self._lock:
            return self._leaf_id

    @property
    def file_path(self) -> Optional[Path]:
        with self._lock:
            return self._store.path if self._store is not None else None

    def save(self, file_path: Path) -> Path:
        """Attach a new file; subsequent changes are appended automatically."""
        path = file_path.expanduser().resolve()
        with self._lock:
            if self._store is not None:
                if path != self._store.path:
                    raise ValueError("This session is already attached to another file")
                # All committed changes have already been persisted.
                return self._store.path
            self._store = JsonlSessionStore.create(path, self._header, self._records)
            return self._store.path

    def get_entries(self) -> list[SessionEntry]:
        with self._lock:
            return [entry.model_copy(deep=True) for entry in self._entries]

    def append_message(self, message: Message, *, task_start: bool = False) -> str:
        with self._lock:
            entry = SessionMessageEntry(
                id=uuid4().hex,
                parent_id=self._leaf_id,
                timestamp=int(time.time() * 1000),
                message=message,
                task_start=task_start,
            )
            return self._append_entry(entry)

    def append_model_change(self, provider: str, model_id: str) -> str:
        with self._lock:
            entry = ModelChangeEntry(
                id=uuid4().hex,
                parent_id=self._leaf_id,
                timestamp=int(time.time() * 1000),
                provider=provider,
                model_id=model_id,
            )
            return self._append_entry(entry)

    def append_compaction(self, entry: CompactionEntry) -> str:
        with self._lock:
            if entry.parent_id != self._leaf_id:
                raise ValueError("Session changed while preparing compaction")
            return self._append_entry(entry)

    def _append_entry(self, entry: SessionEntry) -> str:
        if entry.id in self._by_id:
            raise ValueError(f"Duplicate session entry ID: {entry.id}")
        if entry.parent_id is not None and entry.parent_id not in self._by_id:
            raise ValueError(f"Parent entry does not exist: {entry.parent_id}")
        if isinstance(entry, SessionMessageEntry) and entry.task_start:
            if not isinstance(entry.message, UserMessage):
                raise ValueError("Only a user message can start a task")
        if isinstance(entry, CompactionEntry):
            project_context_entries(self._get_branch(entry.parent_id) + [entry])

        stored = entry.model_copy(deep=True)
        # Commit to disk before reporting success or advancing the in-memory tip.
        if self._store is not None:
            self._store.append(stored)
        self._entries.append(stored)
        self._records.append(stored)
        self._by_id[stored.id] = stored
        self._leaf_id = stored.id
        return stored.id

    def checkout(self, entry_id: Optional[str]) -> None:
        with self._lock:
            if entry_id is not None and entry_id not in self._by_id:
                raise ValueError(f"Session entry does not exist: {entry_id}")
            if entry_id == self._leaf_id:
                return
            record = SessionCursorEntry(
                entry_id=entry_id,
                timestamp=int(time.time() * 1000),
            )
            if self._store is not None:
                self._store.append(record)
            self._records.append(record)
            self._leaf_id = entry_id

    def get_branch(self) -> list[SessionEntry]:
        with self._lock:
            return self._get_branch(self._leaf_id)

    def _get_branch(self, entry_id: Optional[str]) -> list[SessionEntry]:
        if entry_id is not None and entry_id not in self._by_id:
            raise ValueError(f"Session entry does not exist: {entry_id}")

        branch: list[SessionEntry] = []
        current_id = entry_id
        while current_id is not None:
            entry = self._by_id[current_id]
            branch.append(entry.model_copy(deep=True))
            current_id = entry.parent_id
        branch.reverse()
        return branch

    def build_context(self) -> SessionContext:
        with self._lock:
            return self._build_context(self._leaf_id)

    def get_context_entries(self) -> list[ContextMessageEntry]:
        with self._lock:
            return project_context_entries(self._get_branch(self._leaf_id))

    def build_context_at(self, entry_id: Optional[str]) -> SessionContext:
        """Preview a branch without moving the cursor or writing records."""
        with self._lock:
            return self._build_context(entry_id)

    def _build_context(self, entry_id: Optional[str]) -> SessionContext:
        provider: Optional[str] = None
        model_id: Optional[str] = None
        branch = self._get_branch(entry_id)
        for entry in branch:
            if isinstance(entry, ModelChangeEntry):
                provider = entry.provider
                model_id = entry.model_id
        messages = [item.message for item in project_context_entries(branch)]
        return SessionContext(messages=messages, provider=provider, model_id=model_id)
