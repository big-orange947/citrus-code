"""CLI session discovery, launch settings, and an OS-held writer lock."""
from typing import Optional

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

if os.name == "nt":
    import msvcrt
else:
    import fcntl


ShellName = Literal["bash", "powershell"]


class LaunchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    shell: ShellName
    tools: list[str] = Field(min_length=1)


def settings_path(session_path: Path) -> Path:
    return session_path.with_suffix(".settings.json")


def load_settings(session_path: Path) -> Optional[LaunchSettings]:
    path = settings_path(session_path)
    if not path.exists():
        return None
    return LaunchSettings.model_validate_json(path.read_bytes())


def save_settings(session_path: Path, settings: LaunchSettings) -> None:
    with settings_path(session_path).open("x", encoding="utf-8", newline="\n") as file:
        file.write(settings.model_dump_json(indent=2) + "\n")


def session_paths(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime_ns, reverse=True)


@contextmanager
def session_lease(path: Path) -> Iterator[None]:
    """The persistent lock file is reused; the OS releases locks on process exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".jsonl.lock").open("a+b") as file:
        try:
            if os.fstat(file.fileno()).st_size == 0:
                file.write(b"\0")
                file.flush()
            file.seek(0)
            if os.name == "nt":
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                f"无法取得会话写入锁，文件可能已由另一个 CLI 使用：{path}"
            ) from exc
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
