import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...agent.types import ToolExecutionResult
from ...ai.types import TextContent, ToolDefinition
from .processes import find_executable, spawn_process, stop_process
from .truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, truncate_text


class ShellArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command: str = Field(min_length=1, description="Shell command to execute.")
    timeout: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="Timeout in seconds. Omit for no timeout.",
    )


class ShellTool:
    name: str = "bash"
    description: str

    def __init__(self, cwd: Path, shell_path: str | None = None) -> None:
        self._cwd = cwd.resolve()
        self._shell_path = shell_path
        language = "PowerShell" if self.name == "powershell" else "Bash"
        self.description = (
            f"Execute a {language} command in the working directory. Returns stdout "
            "and stderr. Keeps the last 2000 lines or 50KB; full truncated output "
            "is saved to a temporary file. Optional timeout is in seconds."
        )

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=ShellArguments.model_json_schema(),
        )

    def _command(self, command: str) -> list[str]:
        if self.name == "powershell":
            executable = self._shell_path or shutil.which("pwsh") or find_executable("powershell")
            prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
            return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", prefix + command]

        executable = self._shell_path
        if executable is None and os.name == "nt":
            # Prefer Git Bash to Windows' WSL launcher: the tool must execute
            # Bash commands against the same Windows working directory.
            candidates: list[Path] = []
            git = shutil.which("git")
            if git:
                candidates.append(Path(git).resolve().parent.parent / "bin" / "bash.exe")
            for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
                base = os.environ.get(variable)
                if base:
                    candidates.append(Path(base) / "Git" / "bin" / "bash.exe")
            executable = next((str(path) for path in candidates if path.is_file()), None)
            if executable is None:
                raise FileNotFoundError("Git Bash is required on Windows. Install Git Bash or set shell_path.")
        return [executable or find_executable("bash"), "-c", command]

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult:
        args = ShellArguments.model_validate(arguments)
        command = self._command(args.command)
        output_path: Path | None = None
        keep_output = False
        try:
            # A file sink bounds memory and avoids inherited pipe handles keeping
            # a completed foreground shell blocked on stdout/stderr drainage.
            with tempfile.NamedTemporaryFile(
                prefix=f"mini-pi-{self.name}-", suffix=".log", delete=False
            ) as output:
                output_path = Path(output.name)
                process = await spawn_process(
                    command, self._cwd, merge_stderr=True, output=output.file
                )
                timed_out = False
                try:
                    if args.timeout is None:
                        code = await process.wait()
                    else:
                        code = await asyncio.wait_for(process.wait(), args.timeout)
                except TimeoutError:
                    timed_out = True
                    await stop_process(process)
                    code = await process.wait()
                except BaseException:
                    await stop_process(process)
                    await process.wait()
                    raise

            format_task = asyncio.create_task(asyncio.to_thread(self._format_output, output_path))
            try:
                text, details = await asyncio.shield(format_task)
            except asyncio.CancelledError:
                # Do not remove an output file still being read by the worker.
                await format_task
                raise
            keep_output = "fullOutputPath" in details
            if timed_out:
                raise RuntimeError(f"{text}\n\nCommand timed out after {args.timeout} seconds")
            if code != 0:
                raise RuntimeError(f"{text}\n\nCommand exited with code {code}")
            details["exit_code"] = code
            return ToolExecutionResult(content=[TextContent(text=text)], details=details)
        finally:
            if output_path is not None and not keep_output:
                output_path.unlink(missing_ok=True)

    def _format_output(self, path: Path) -> tuple[str, dict[str, JsonValue]]:
        total_bytes = 0
        newline_count = 0
        last_byte = b""
        suffix = bytearray()
        with path.open("rb") as file:
            while chunk := file.read(64 * 1024):
                total_bytes += len(chunk)
                newline_count += chunk.count(b"\n")
                last_byte = chunk[-1:]
                suffix.extend(chunk)
                if len(suffix) > DEFAULT_MAX_BYTES:
                    del suffix[:-DEFAULT_MAX_BYTES]

        byte_clipped = total_bytes > len(suffix)
        partial_line = False
        if byte_clipped:
            first_break = suffix.find(b"\n")
            if 0 <= first_break < len(suffix) - 1:
                del suffix[:first_break + 1]
            else:
                partial_line = True
        # Ignore only an incomplete leading UTF-8 code point introduced by clipping.
        while suffix and byte_clipped and suffix[0] & 0xC0 == 0x80:
            del suffix[0]
        tail = bytes(suffix).decode("utf-8", errors="replace")
        truncation = truncate_text(tail, tail=True)
        truncation.total_bytes = total_bytes
        truncation.total_lines = newline_count + int(total_bytes > 0 and last_byte != b"\n")
        if byte_clipped:
            truncation.truncated = True
            if truncation.truncated_by is None:
                truncation.truncated_by = "bytes"
            truncation.last_line_partial = partial_line

        text = truncation.content or "(no output)"
        details: dict[str, JsonValue] = {}
        if truncation.truncated:
            details["truncation"] = truncation.model_dump(mode="json", exclude={"content"})
            details["fullOutputPath"] = str(path)
            text += (
                f"\n\n[Showing the last {truncation.output_lines} lines "
                f"({truncation.output_bytes} bytes); limits: {DEFAULT_MAX_LINES} lines / "
                f"{DEFAULT_MAX_BYTES} bytes. Full output: {path}]"
            )
        return text, details
