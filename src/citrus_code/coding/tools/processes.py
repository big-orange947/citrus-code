import asyncio
import os
import shutil
import signal
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO


def find_executable(name: str) -> str:
    """Use PATH or pi's already-installed helpers; never download executables."""
    found = shutil.which(name)
    if found:
        return found
    suffix = ".exe" if os.name == "nt" else ""
    bundled = Path.home() / ".pi" / "agent" / "bin" / f"{name}{suffix}"
    if bundled.is_file():
        return str(bundled)
    raise FileNotFoundError(f"{name} is required. Install it and add it to PATH.")


async def spawn_process(
    command: list[str],
    cwd: Path,
    *,
    merge_stderr: bool = False,
    output: BinaryIO | None = None,
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=output if output is not None else asyncio.subprocess.PIPE,
        stderr=(asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE),
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        limit=8 * 1024 * 1024,
    )


async def stop_process(process: asyncio.subprocess.Process) -> None:
    """Stop a running command and its process tree on cancellation or timeout."""
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(process.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        await killer.wait()
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def command_lines(
    command: list[str],
    cwd: Path,
    *,
    success_codes: tuple[int, ...] = (0,),
) -> AsyncIterator[str]:
    """Stream search output; closing the generator also reaps its child process."""
    process = await spawn_process(command, cwd)
    stderr = bytearray()

    async def drain_errors() -> None:
        if process.stderr is None:
            return
        while chunk := await process.stderr.read(8192):
            if len(stderr) < 50 * 1024:
                stderr.extend(chunk[:50 * 1024 - len(stderr)])

    error_task = asyncio.create_task(drain_errors())
    try:
        if process.stdout is None:
            raise RuntimeError("The process has no stdout pipe")
        async for line in process.stdout:
            yield line.decode("utf-8", errors="replace").rstrip("\r\n")
        code = await process.wait()
        await error_task
        if code not in success_codes:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(message or f"Command exited with code {code}")
    finally:
        await stop_process(process)
        if process.stdout is not None:
            # A killed child may leave unread bytes in a paused pipe. Drain them
            # before waiting for asyncio's process transport to close.
            await process.stdout.read()
        await process.wait()
        if not error_task.done():
            error_task.cancel()
        await asyncio.gather(error_task, return_exceptions=True)
