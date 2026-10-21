"""Compact welcome panel with centered branding and two divided columns."""

from pathlib import Path

from rich.align import Align
from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .theme import ACCENT, LOGO, clean_text


def build_welcome(cwd: Path, model_id: str, *, resumed: bool, width: int) -> Panel:
    width = max(1, min(width, 110))
    logo = Text(LOGO, style="accent", no_wrap=True)
    logo.stylize("welcome.leaf", 0, LOGO.index("\n"))
    left = Group(
        Align.center(Text("欢迎回来！" if resumed else "欢迎使用 Citrus Code", style="bold")),
        Text(),
        Align.center(logo),
        Text(
            f"{clean_text(model_id)} · 1M 上下文",
            style="muted", justify="center", no_wrap=True, overflow="ellipsis",
        ),
        Text(clean_text(str(cwd)), style="muted", justify="center", no_wrap=True, overflow="ellipsis"),
    )
    right = Group(
        Text("开始使用", style="accent"),
        Text("输入 / 查看命令，直接描述任务即可开始。", no_wrap=True, overflow="ellipsis"),
        Rule(style="welcome.divider", characters="─"),
        Text("当前会话", style="accent"),
        Text("已恢复，可以继续上次任务。" if resumed else "新会话，等待你的第一个任务。", style="muted"),
    )
    body: RenderableType
    if width >= 82:
        grid = Table.grid(expand=True, padding=0)
        grid.add_column(ratio=44)
        grid.add_column(width=1)
        grid.add_column(ratio=56)
        # Four non-logo rows plus one row of top padding.
        height = LOGO.count("\n") + 6
        grid.add_row(
            Padding(left, (1, 1, 0, 1)),
            Text("\n".join("│" for _ in range(height)), style="welcome.divider"),
            Padding(right, (0, 0, 0, 1)),
        )
        body = grid
    else:
        body = Group(left, Rule(style="welcome.divider", characters="─"), right)
    return Panel(
        body,
        title=Text.assemble((" Citrus Code ", "accent"), ("v0.1.0 ", "muted")),
        title_align="left", border_style=ACCENT, padding=(0, 1), width=width,
    )
