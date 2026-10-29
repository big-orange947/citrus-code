import unittest
from types import SimpleNamespace
from unittest.mock import patch

from prompt_toolkit.data_structures import Point, Size
from prompt_toolkit.layout.screen import Char, Screen

from citrus_code.cli.resize import install_resize_reflow_fix


class ResizeReflowTest(unittest.TestCase):
    def make_app(self, new_width, *, fullscreen=False, cursor=Point(x=2, y=1)):
        screen = Screen(initial_height=4)
        for x in range(98):
            screen.data_buffer[0][x] = Char("─")
        renderer = SimpleNamespace(
            full_screen=fullscreen,
            last_rendered_screen=screen,
            _last_size=Size(rows=24, columns=100),
            output=SimpleNamespace(get_size=lambda: Size(rows=24, columns=new_width)),
            _cursor_pos=cursor,
        )
        observed = []
        app = SimpleNamespace(renderer=renderer, _on_resize=lambda: observed.append(renderer._cursor_pos))
        with patch("citrus_code.cli.resize.os.name", "nt"):
            install_resize_reflow_fix(app)
        return app, observed

    def test_shrink_accounts_for_wrapped_border(self):
        app, observed = self.make_app(60)
        app._on_resize()
        self.assertEqual(observed, [Point(x=2, y=2)])

    def test_cursor_line_wrap_and_previous_rows_are_both_counted(self):
        app, observed = self.make_app(40, cursor=Point(x=85, y=1))
        app._on_resize()
        self.assertEqual(observed, [Point(x=5, y=5)])

    def test_expansion_and_fullscreen_keep_original_cursor(self):
        for width, fullscreen in ((110, False), (60, True)):
            with self.subTest(width=width, fullscreen=fullscreen):
                app, observed = self.make_app(width, fullscreen=fullscreen)
                app._on_resize()
                self.assertEqual(observed, [Point(x=2, y=1)])


if __name__ == "__main__":
    unittest.main()
