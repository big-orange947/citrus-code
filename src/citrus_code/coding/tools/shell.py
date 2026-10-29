from typing import Optional
import asyncio
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...agent.types import ToolExecutionResult
from ...ai.types import TextContent, ToolDefinition
from .local_shell import LocalShellOperations
from .operations import ShellOperations
from .truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, truncate_text


class ShellArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command: str = Field(min_length=1, description="Shell command to execute.")
    timeout: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="Timeout in seconds. Omit for no timeout.",
    )


class ShellTool:
    name: str = "bash"
    description: str

    def __init__(
        self, cwd: Path, shell_path: Optional[str] = None, *, operations: Optional[ShellOperations] = None,
    ) -> None:
        self._cwd = cwd
        self._shell_path = shell_path
        self._ops = LocalShellOperations() if operations is None else operations
        language = "PowerShell" if self.name == "powershell" else "Bash"
        self.description = (
            f"Execute a {language} command in the working directory. Returns stdout "
            "and stderr. Keeps the last 2000 lines or 50KB; full truncated output "
            "is saved to a temporary file. Optional timeout is in seconds."
        )
        if self.name == "powershell":
            self.description += (
                " Commands run in a fresh session; variables and cd do not persist. "
                "Use Windows PowerShell 5.1-compatible syntax: no Bash heredocs, && or ||. "
                "Backslash does not escape quotes in PowerShell; use single-quoted literals "
                "or a PowerShell here-string. For complex Python/JavaScript, write a script "
                "file first, then execute it instead of nesting code inside -c/-e."
            )

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=ShellArguments.model_json_schema(),
        )

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult:
        args = ShellArguments.model_validate(arguments)
        output_path: Optional[Path] = None
        keep_output = False
        try:
            # A file sink bounds memory and avoids inherited pipe handles keeping
            # a completed foreground shell blocked on stdout/stderr drainage.
            with tempfile.NamedTemporaryFile(
                prefix=f"citrus-code-{self.name}-", suffix=".log", delete=False
            ) as output:
                output_path = Path(output.name)
                result = await self._ops.execute(
                    args.command, self._cwd,
                    shell="powershell" if self.name == "powershell" else "bash",
                    shell_path=self._shell_path, timeout=args.timeout, output=output.file,
                )

            format_task = asyncio.create_task(asyncio.to_thread(self._format_output, output_path))
            try:
                text, details = await asyncio.shield(format_task)
            except asyncio.CancelledError:
                # Do not remove an output file still being read by the worker.
                await format_task
                raise
            keep_output = details.get("fullOutputPath") == str(output_path)
            if result.timed_out:
                raise RuntimeError(
                    f"{text}\n\nCommand timed out after {args.timeout} seconds"
                    f"\nShell: {result.shell}"
                )
            if result.exit_code != 0:
                raise RuntimeError(f"{text}\n\nCommand exited with code {result.exit_code}\nShell: {result.shell}")
            details["exit_code"] = result.exit_code
            details["shell"] = result.shell
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
            reference = self._ops.persist_output(path, self._cwd)
            if not reference:
                raise ValueError("Shell operations returned an empty output path")
            details["truncation"] = truncation.model_dump(mode="json", exclude={"content"})
            details["fullOutputPath"] = reference
            text += (
                f"\n\n[Showing the last {truncation.output_lines} lines "
                f"({truncation.output_bytes} bytes); limits: {DEFAULT_MAX_LINES} lines / "
                f"{DEFAULT_MAX_BYTES} bytes. Full output: {reference}]"
            )
        return text, details
