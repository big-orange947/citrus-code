import unittest
from io import StringIO
from types import SimpleNamespace

from prompt_toolkit.data_structures import Point
from rich.panel import Panel

from citrus_code.cli.output import TerminalOutput
from citrus_code.cli.preview import TranscriptControl
from citrus_code.cli.render import TranscriptRichConsole


class ManagedTerminalOutputTest(unittest.IsolatedAsyncioTestCase):
    async def test_transcript_is_retained_for_redraw_and_restored_on_exit(self):
        original = StringIO()
        invalidations = []
        prompt = SimpleNamespace(app=SimpleNamespace(invalidate=lambda: invalidations.append(True)))
        output = TerminalOutput(original, prompt, managed_screen=True)
        output.write("first\nsecond\n")
        await output.drain()
        self.assertEqual(original.getvalue(), "")
        self.assertEqual(output.transcript_cursor(), Point(x=0, y=2))
        output.scroll(-1)
        self.assertEqual(output.transcript_cursor(), Point(x=0, y=1))
        output.write("third\n")
        await output.drain()
        self.assertEqual(output.transcript_cursor(), Point(x=0, y=3))
        output.restore_transcript()
        self.assertEqual(original.getvalue(), "first\nsecond\nthird\n")
        self.assertEqual(len(invalidations), 3)

    async def test_transcript_control_reuses_cached_lines_when_scrolling(self):
        prompt = SimpleNamespace(app=SimpleNamespace(invalidate=lambda: None))
        output = TerminalOutput(StringIO(), prompt, managed_screen=True)
        output.write("first\nsecond\n")
        await output.drain()
        control = TranscriptControl(output)
        lines = output.transcript_lines()
        output.scroll(-1)
        content = control.create_content(80, 20)
        self.assertIs(output.transcript_lines(), lines)
        self.assertEqual("".join(text for _, text in content.get_line(1)), "second")
        self.assertEqual(content.cursor_position, Point(x=0, y=1))

    async def test_panel_fits_the_available_transcript_width(self):
        stream = StringIO()
        console = TranscriptRichConsole(file=stream, force_terminal=True, color_system=None, width=100, legacy_windows=False)
        console.width_provider = lambda: 78
        console.print(Panel("tool output"))
        lines = stream.getvalue().splitlines()
        self.assertTrue(all(len(line) == 78 for line in lines))
        self.assertTrue(all(line.endswith(end) for line, end in zip(lines, ("╮", "│", "╯"))))


if __name__ == "__main__":
    unittest.main()
