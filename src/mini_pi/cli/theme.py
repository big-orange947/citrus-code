"""Shared terminal colors, original pi emblem, and safe display text."""

import re

from prompt_toolkit.styles import Style
from rich.theme import Theme


ACCENT = "#d79570"
MUTED = "#8b929c"
BORDER = "#50555e"
SUCCESS = "#98c379"
ERROR = "#e06c75"
INFO = "#82b9d6"

LOGO = "\n".join((
    "  ▄▄▄▄▄▄▄▄▄",
    "  ▀██▀▀▀██▀",
    "   ██   ██ ",
    "   ██   ██▄",
    "   ▀▀   ▀▀▀",
))

RICH_THEME = Theme({
    "accent": f"bold {ACCENT}", "muted": MUTED, "border": BORDER,
    "success": SUCCESS, "error": ERROR, "info": INFO,
    "markdown.h1": f"bold {ACCENT}", "markdown.h2": f"bold {ACCENT}",
    "markdown.h3": f"bold {INFO}", "markdown.code": f"{ACCENT} on #25272c",
    "markdown.link": f"underline {INFO}", "markdown.link_url": MUTED,
    "markdown.block_quote": MUTED,
})

PROMPT_STYLE = Style.from_dict({
    "prompt": f"bold {ACCENT}", "placeholder": MUTED,
    "frame.border": BORDER, "bottom-toolbar": "noreverse",
    "bottom-toolbar.text": MUTED, "status": SUCCESS,
    "status.busy": ACCENT, "status.dim": MUTED,
    "completion-menu.completion": "bg:#25272c #c9cdd3",
    "completion-menu.completion.current": f"bg:#45404a {ACCENT}",
    "auto-suggestion": MUTED,
})

_CONTROL_SEQUENCES = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-_])"
    r"|[\x00-\x08\x0b-\x1f\x7f-\x9f]"
)


def clean_text(text: str) -> str:
    """Keep newlines and tabs, but strip embedded terminal commands."""
    return _CONTROL_SEQUENCES.sub("", text)
