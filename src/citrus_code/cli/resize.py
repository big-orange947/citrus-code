"""Account for Windows terminal reflow before prompt_toolkit erases its input."""

import os

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point


def install_resize_reflow_fix(app: Application) -> None:
    if os.name != "nt":
        return
    original = app._on_resize

    def on_resize() -> None:
        renderer = app.renderer
        screen = renderer.last_rendered_screen
        previous_size = renderer._last_size
        current_size = renderer.output.get_size()
        cursor = renderer._cursor_pos
        if (
            not renderer.full_screen
            and screen is not None
            and previous_size is not None
            and current_size.columns < previous_size.columns
        ):
            width = max(1, current_size.columns)
            extra_rows = 0
            for row in range(min(cursor.y, screen.height)):
                cells = screen.data_buffer[row]
                used = max((column + char.width for column, char in cells.items() if char.char.strip()), default=0)
                if used:
                    extra_rows += (used - 1) // width
            extra_rows += cursor.x // width
            if extra_rows:
                # ConPTY wraps existing rows before prompt_toolkit erases them.
                # Move back over those extra rows so erase_down starts at the
                # original top of the prompt, not in the middle of a border.
                renderer._cursor_pos = Point(x=cursor.x % width, y=cursor.y + extra_rows)
        original()

    app._on_resize = on_resize
