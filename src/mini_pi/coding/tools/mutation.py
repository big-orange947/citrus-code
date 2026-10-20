import os
from _thread import LockType
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock


@dataclass
class _Entry:
    lock: LockType = field(default_factory=Lock)
    users: int = 0


_entries: dict[str, _Entry] = {}
_registry_lock = Lock()


@contextmanager
def file_mutation(path: Path) -> Iterator[None]:
    """Serialize writes to one path inside the actual worker thread.

    Cancelling the awaiting coroutine cannot release this lock while the
    filesystem operation is still running. Different paths use different locks.
    """
    key = os.path.normcase(str(path.resolve()))
    with _registry_lock:
        entry = _entries.setdefault(key, _Entry())
        entry.users += 1

    try:
        with entry.lock:
            yield
    finally:
        with _registry_lock:
            entry.users -= 1
            if entry.users == 0:
                del _entries[key]
