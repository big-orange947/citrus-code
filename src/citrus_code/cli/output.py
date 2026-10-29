"""Flush Rich transcript output while the prompt renderer is suspended."""

import asyncio
from io import StringIO
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.formatted_text.utils import split_lines
from typing import TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal


class TerminalOutput(StringIO):
    def __init__(self, original: TextIO, prompt: PromptSession[str], *, managed_screen: bool = False) -> None:
        super().__init__()
        self.original = original
        self.prompt = prompt
        self._drain_lock = asyncio.Lock()
        self.managed_screen = managed_screen
        self._transcript = ""
        self._transcript_ansi = ANSI("")
        self._transcript_lines = [[]]
        self._line_count = 0
        self._scroll_line = None

    def transcript(self) -> ANSI:
        return self._transcript_ansi

    def transcript_lines(self):
        return self._transcript_lines

    def transcript_cursor(self) -> Point:
        end = self._line_count
        return Point(x=0, y=end if self._scroll_line is None else min(self._scroll_line, end))

    def scroll(self, lines: int) -> None:
        end = self._line_count
        current = end if self._scroll_line is None else self._scroll_line
        target = max(0, min(end, current + lines))
        self._scroll_line = None if target == end else target
        self.prompt.app.invalidate()

    def restore_transcript(self) -> None:
        if self.managed_screen and self._transcript:
            self.original.write(self._transcript)
            self.original.flush()

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

            if self.managed_screen:
                self._transcript += text
                self._transcript_ansi = ANSI(self._transcript)
                self._transcript_lines = list(split_lines(to_formatted_text(self._transcript_ansi)))
                self._line_count += text.count("\n")
                self._scroll_line = None
                self.prompt.app.invalidate()
                return

            def write() -> None:
                output = self.prompt.app.output
                output.enable_autowrap()
                output.write_raw(text)
                output.flush()

            await run_in_terminal(write)
