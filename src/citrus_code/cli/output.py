"""Flush Rich transcript output while the prompt renderer is suspended."""

import asyncio
from io import StringIO
from typing import TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal


class TerminalOutput(StringIO):
    def __init__(self, original: TextIO, prompt: PromptSession[str]) -> None:
        super().__init__()
        self.original = original
        self.prompt = prompt
        self._drain_lock = asyncio.Lock()

    @property
    def encoding(self) -> str:
        return self.original.encoding or "utf-8"

    def isatty(self) -> bool:
        return self.original.isatty()

    # Keep StringIO.fileno unsupported: Rich must not bypass the buffer via
    # the Windows console handle while prompt_toolkit is drawing the input.
    async def drain(self) -> None:
        async with self._drain_lock:
            text = self.getvalue()
            if not text:
                return
            self.seek(0)
            self.truncate(0)

            def write() -> None:
                output = self.prompt.app.output
                output.enable_autowrap()
                output.write_raw(text)
                output.flush()

            await run_in_terminal(write)
