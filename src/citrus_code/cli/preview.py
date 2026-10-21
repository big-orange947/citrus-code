"""A bounded Markdown preview rendered by the existing prompt application."""

from prompt_toolkit import PromptSession
from prompt_toolkit.filters import Condition, has_focus, to_filter
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.layout import ConditionalContainer, FloatContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu, MultiColumnCompletionsMenu

from .commands import is_command_input
from .render import Console


class MarkdownPreview:
    def __init__(self, console: Console, prompt: PromptSession[str]) -> None:
        self.console = console
        self.prompt = prompt
        self._cache_key: tuple[str, int, int] | None = None
        self._cache = ANSI("")
        self._welcome_key: tuple[int, bool] | None = None
        self._welcome_cache = ANSI("")
        original = prompt.layout
        self._input_window = original.current_window
        # Measure actual input rows, including wrapping, instead of consuming
        # all remaining terminal height between the two separators.
        original.current_window.height = Dimension(min=1)
        original.current_window.dont_extend_height = to_filter(True)
        preview = ConditionalContainer(
            HSplit([
                Window(FormattedTextControl(self._header), height=1),
                ConditionalContainer(
                    Window(
                        FormattedTextControl(self._text), dont_extend_height=True,
                        wrap_lines=False, always_hide_cursor=True,
                    ),
                    filter=Condition(lambda: bool(console.response)),
                ),
                Window(height=1),
            ]),
            filter=Condition(lambda: (
                (console.is_busy or bool(console.response)) and not prompt.app.is_done
                and prompt.app.output.get_size().rows >= 12 + self._menu_height()
            )),
        )
        root = original.container
        if not isinstance(root, HSplit):
            raise TypeError("Expected PromptSession to provide a vertical input layout")
        # Place completion rows below the lower separator. Keep the original
        # input, search, validation and history controls, but remove floating menus.
        for container in list(original.walk()):
            if isinstance(container, FloatContainer):
                container.floats = [
                    floating for floating in container.floats
                    if not isinstance(floating.content, (CompletionsMenu, MultiColumnCompletionsMenu))
                ]
        content = HSplit([
            ConditionalContainer(
                HSplit([
                    Window(
                        FormattedTextControl(self._welcome), dont_extend_height=True,
                        wrap_lines=False, always_hide_cursor=True,
                    ),
                    Window(height=1),
                ]),
                filter=Condition(lambda: console.shows_welcome and not prompt.app.is_done),
            ),
            preview,
            Window(height=1, char="─", style="class:input.separator"),
            *root.children[:-1],
            Window(height=1, char="─", style="class:input.separator"),
            CompletionsMenu(
                max_height=6, scroll_offset=1,
                extra_filter=has_focus(prompt.default_buffer),
            ),
            root.children[-1],
        ])
        # The final column is a gutter, avoiding terminal autowrap at the edge.
        prompt.layout = Layout(VSplit([content, Window(width=1)]), original.current_window)
        prompt.app.layout = prompt.layout
        console.set_preview_listener(prompt.app.invalidate)

    def _welcome(self) -> ANSI:
        size = self.prompt.app.output.get_size()
        width = max(1, size.columns - 1)
        minimum_rows = 18 if min(width, 110) >= 82 else 24
        input_rows = self._input_window.preferred_height(width, size.rows).preferred
        compact = (
            size.rows < minimum_rows + self._menu_height() + max(0, input_rows - 1)
            or width < 28
        )
        key = (width, compact)
        if key != self._welcome_key:
            self._welcome_cache = ANSI(self.console.welcome_ansi(width, compact=compact))
            self._welcome_key = key
        return self._welcome_cache

    def _header(self) -> FormattedText:
        return FormattedText([
            ("class:prompt", " Citrus Code"),
            ("class:status.busy", " · " + self.console.activity),
        ])

    def _menu_height(self) -> int:
        buffer = self.prompt.default_buffer
        return 6 if buffer.complete_state is not None or is_command_input(buffer.document) else 0

    def _text(self) -> ANSI:
        size = self.prompt.app.output.get_size()
        width = max(10, size.columns - 2)
        height = max(2, min(18, size.rows - 11 - self._menu_height()))
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
