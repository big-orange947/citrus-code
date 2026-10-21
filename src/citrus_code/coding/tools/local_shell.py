"""Default command operations, including Windows PowerShell transport."""

import asyncio
import base64
import os
from pathlib import Path
import shutil
import tempfile
from typing import BinaryIO

from .operations import ShellName, ShellResult
from .processes import find_executable, spawn_process, stop_process


class LocalShellOperations:
    def _command(
        self,
        command: str,
        shell: ShellName,
        shell_path: str | None,
        script_path: Path | None,
    ) -> list[str]:
        if shell == "powershell":
            executable = shell_path or shutil.which("pwsh") or find_executable("powershell")
            if script_path is None:
                raise ValueError("PowerShell requires a script file")
            encoded_path = base64.b64encode(str(script_path).encode("utf-8")).decode("ascii")
            # Parse the command only after output encoding and error handling
            # are set. Only this short launcher crosses Windows argv parsing.
            launcher = (
                "$OutputEncoding = [Console]::OutputEncoding = "
                "[System.Text.UTF8Encoding]::new($false); "
                "$ErrorActionPreference = 'Stop'; "
                "$PSNativeCommandUseErrorActionPreference = $false; "
                "try { "
                "$citrusCodeScriptPath = [System.Text.Encoding]::UTF8.GetString("
                f"[System.Convert]::FromBase64String('{encoded_path}')); "
                "& ([scriptblock]::Create([System.IO.File]::ReadAllText("
                "$citrusCodeScriptPath, [System.Text.Encoding]::UTF8))); "
                "if (-not $?) { "
                "if ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; "
                "exit 1 }; exit 0 "
                "} catch { [Console]::Error.WriteLine(($_ | Out-String)); exit 1 }"
            )
            return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", launcher]

        executable = shell_path
        if executable is None and os.name == "nt":
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
        command: str,
        cwd: Path,
        *,
        shell: ShellName,
        shell_path: str | None,
        timeout: float | None,
        output: BinaryIO,
    ) -> ShellResult:
        script_path: Path | None = None
        try:
            if shell == "powershell":
                with tempfile.NamedTemporaryFile(
                    prefix="citrus-code-powershell-", suffix=".ps1",
                    mode="w", encoding="utf-8", newline="\n", delete=False,
                ) as script:
                    script_path = Path(script.name)
                    script.write(command)
                    # Capture status in the same block as the user command.
                    script.write(
                        "\nif (-not $?) { "
                        "if ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; "
                        "exit 1 }; exit 0\n"
                    )
            argv = self._command(command, shell, shell_path, script_path)
            process = await spawn_process(argv, cwd, merge_stderr=True, output=output)
            timed_out = False
            try:
                if timeout is None:
                    code = await process.wait()
                else:
                    code = await asyncio.wait_for(process.wait(), timeout)
            except TimeoutError:
                timed_out = True
                await stop_process(process)
                code = await process.wait()
            except BaseException:
                await stop_process(process)
                await process.wait()
                raise
            return ShellResult(exit_code=code, shell=argv[0], timed_out=timed_out)
        finally:
            if script_path is not None:
                script_path.unlink(missing_ok=True)

    def persist_output(self, local_path: Path, cwd: Path) -> str:
        return str(local_path)
