"""A bounded Markdown preview rendered by the existing prompt application."""

from prompt_toolkit import PromptSession
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from .render import Console


class MarkdownPreview:
    def __init__(self, console: Console, prompt: PromptSession[str]) -> None:
        self.console = console
        self.prompt = prompt
        self._cache_key: tuple[str, int, int] | None = None
        self._cache = ANSI("")
        original = prompt.layout
        preview = ConditionalContainer(
            HSplit([
                Window(FormattedTextControl(FormattedText([
                    ("class:prompt", " mini-pi"),
                    ("class:status.dim", " · 正在生成"),
                ])), height=1),
                Window(
                    FormattedTextControl(self._text), dont_extend_height=True,
                    wrap_lines=False, always_hide_cursor=True,
                ),
                Window(height=1),
            ]),
            filter=Condition(lambda: (
                bool(console.response) and not prompt.app.is_done
                and prompt.app.output.get_size().rows >= 12
            )),
        )
        # Keep the existing buffers, completion menus, search and key bindings.
        prompt.layout = Layout(HSplit([preview, original.container]), original.current_window)
        prompt.app.layout = prompt.layout
        console.set_preview_listener(prompt.app.invalidate)

    def _text(self) -> ANSI:
        size = self.prompt.app.output.get_size()
        width = max(10, size.columns - 2)
        height = max(2, min(18, size.rows - 11))
        key = (self.console.response, width, height)
        if key != self._cache_key:
            lines = self.console.preview_ansi(width).splitlines()
            if len(lines) > height:
                lines = ["… 较早内容将在生成完成后显示"] + lines[-(height - 1):]
            self._cache = ANSI("\n".join(" " + line for line in lines))
            self._cache_key = key
        return self._cache

    def close(self) -> None:
        self.console.set_preview_listener(None)
