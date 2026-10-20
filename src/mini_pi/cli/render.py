"""Rich transcript rendering; the terminal owns interactive cursor movement."""

import time
from collections.abc import Callable
from io import StringIO
from pathlib import Path

from rich.console import Console as RichConsole, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ..agent.events import (
    AgentEndEvent, AgentEvent, AssistantMessageCommittedEvent,
    ContextCompactedEvent, MessageAcceptedEvent, ModelEvent,
    ToolExecutionEndEvent, ToolExecutionStartEvent, TurnStartEvent,
)
from ..ai.events import DoneEvent, ErrorEvent, StartEvent, TextDeltaEvent, TextStartEvent, ToolCallStartEvent
from ..ai.types import Message, ModelSpec, TextContent, ToolCall
from ..coding.coding_session import CodingSession
from ..coding.session import CompactionEntry, SessionMessageEntry
from .theme import ACCENT, BORDER, LOGO, RICH_THEME, clean_text


def message_text(message: Message) -> str:
    if isinstance(message.content, str):
        return clean_text(message.content)
    return clean_text("\n".join(block.text for block in message.content if isinstance(block, TextContent)))


def markdown(text: str) -> Markdown:
    return Markdown(clean_text(text), code_theme="monokai", hyperlinks=False)


class Console:
    def __init__(self, verbose: bool = False, *, max_output_tokens: int | None = None) -> None:
        self.verbose = verbose
        self.max_output_tokens = max_output_tokens
        self.rich = RichConsole(theme=RICH_THEME, markup=False, highlight=False, emoji=False)
        self.model_name = ""
        self.supports_images = False
        self.phase = "就绪"
        self._response = ""
        self._preview_listener: Callable[[], None] | None = None
        self._live: Live | None = None
        self._started = 0.0
        self._tool_started: dict[str, float] = {}
        self._input_tokens = 0
        self._output_tokens = 0

    @property
    def response(self) -> str:
        return self._response

    def set_preview_listener(self, listener: Callable[[], None] | None) -> None:
        self._preview_listener = listener

    def _refresh(self) -> None:
        if self._preview_listener is not None:
            self._preview_listener()
        elif self._response and self.rich.is_terminal and not self.rich.is_dumb_terminal:
            content = Group(Text("mini-pi", style="accent"), markdown(self._response))
            if self._live is None:
                self._live = Live(
                    content, console=self.rich, transient=True, refresh_per_second=8,
                    redirect_stdout=False, redirect_stderr=False,
                )
                self._live.start()
            else:
                self._live.update(content)

    def finish_response(self, text: str | None = None) -> None:
        content = self._response if text is None else text
        self._response = ""
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._refresh()
        if content.strip():
            self.rich.print(Padding(Group(
                Text("mini-pi", style="accent"), markdown(content),
            ), (1, 1, 0, 1)))

    def preview_ansi(self, width: int) -> str:
        """Format a frame only; no cursor control or direct terminal output."""
        buffer = StringIO()
        renderer = RichConsole(
            file=buffer, width=max(10, width), force_terminal=True,
            color_system="truecolor", theme=RICH_THEME, markup=False,
            highlight=False, emoji=False,
        )
        renderer.print(markdown(self._response))
        return buffer.getvalue()

    def welcome(
        self, cwd: Path, model: ModelSpec, tools: list[str], session_path: Path,
        context_paths: list[Path], *, fake: bool, resumed: bool, interactive: bool,
    ) -> None:
        self.model_name = model.id
        self.supports_images = model.supports_images
        if interactive:
            left = Group(
                Text(LOGO, style="accent"), Text(),
                Text("mini-pi · Python coding agent", style="bold"),
                Text(model.id + (" · 图像已启用" if model.supports_images else ""), style="muted"),
                Text(str(cwd), style="muted"),
            )
            right = Group(
                Text("继续你的项目" if resumed else "从一个具体任务开始", style="accent"),
                Text(), Text("/help       查看全部命令"),
                Text("/history    回看当前对话"), Text("/tree       查看与切换分支"),
                Text(), Text("Enter 发送 · Alt+Enter 换行", style="muted"),
                Text("Ctrl+C 取消 · Ctrl+D 退出", style="muted"),
            )
            if self.rich.width >= 82:
                grid = Table.grid(expand=True, padding=(0, 2))
                grid.add_column(ratio=1)
                grid.add_column(ratio=1)
                grid.add_row(left, right)
                self.rich.print(Panel(
                    grid, title=Text(" mini-pi v0.1.0 ", style="accent"),
                    title_align="left", border_style=ACCENT, padding=(1, 2),
                ))
            else:
                self.rich.print(Panel(
                    Group(left, Text(), right), title=Text(" mini-pi v0.1.0 ", style="accent"),
                    title_align="left", border_style=ACCENT, padding=(1, 1),
                ))
        else:
            self.rich.print(Text.assemble(("mini-pi", "accent"), f"  {model.id}  ", (str(cwd), "muted")))
        details = Text("工具  ", style="muted")
        details.append(" · ".join(tools))
        details.append("\n项目指令  ", style="muted")
        details.append(" · ".join(str(path) for path in context_paths) or "无")
        details.append("\n会话  ", style="muted")
        details.append(str(session_path))
        details.append("\n上下文  1M · 60% 自动压缩", style="muted")
        if self.max_output_tokens is not None:
            details.append(f"\n单次输出上限  {self.max_output_tokens:,} tokens", style="muted")
        self.rich.print(Padding(details, (0, 1, 1, 1)))
        if fake:
            self.note("[离线模式] 只输出预设文字，不执行编程任务。")

    def begin_turn(self, text: str) -> None:
        self.finish_response()
        self._started = time.monotonic()
        self._input_tokens = self._output_tokens = 0
        self.phase = "思考中"
        self.rich.print(Panel(
            Text(clean_text(text)), title=Text("你", style="info"),
            title_align="left", border_style=BORDER, padding=(0, 1),
        ))
        self._refresh()

    def note(self, text: str) -> None:
        style = "error" if any(word in text for word in ("失败", "错误")) else "muted"
        self.rich.print(Padding(Text(clean_text(text), style=style), (0, 1)))

    def event(self, event: AgentEvent) -> None:
        if isinstance(event, TurnStartEvent):
            self.phase = "思考中"
        elif isinstance(event, ModelEvent):
            model_event = event.event
            if isinstance(model_event, StartEvent):
                self.phase = "生成中"
                self._response = ""
            elif isinstance(model_event, TextStartEvent):
                if self._response:
                    self._response += "\n\n"
            elif isinstance(model_event, TextDeltaEvent):
                self._response += clean_text(model_event.delta)
            elif isinstance(model_event, ToolCallStartEvent):
                self.phase = "准备工具调用"
            elif isinstance(model_event, DoneEvent):
                self.finish_response(message_text(model_event.message))
            elif isinstance(model_event, ErrorEvent):
                self.finish_response(message_text(model_event.error) or self._response)
        elif isinstance(event, AssistantMessageCommittedEvent):
            if event.message.usage is not None:
                self._input_tokens += event.message.usage.input_tokens
                self._output_tokens += event.message.usage.output_tokens
        elif isinstance(event, MessageAcceptedEvent):
            label = "引导消息已接纳" if event.source == "steering" else "后续任务已接纳"
            self.rich.print(Panel(Text(message_text(event.message)), title=label, border_style="info"))
        elif isinstance(event, ToolExecutionStartEvent):
            self.phase = f"执行 {event.call.name}"
            self._tool_started[event.call.id] = time.monotonic()
            line = Text(f"  {event.call.name}  ", style="info")
            line.append(self._call_summary(event.call), style="muted")
            self.rich.print(line)
        elif isinstance(event, ToolExecutionEndEvent):
            self._tool_result(event)
            self.phase = "思考中"
        elif isinstance(event, ContextCompactedEvent):
            self.note(f"上下文已压缩  {event.tokens_before:,} → {event.tokens_after:,} tokens；继续当前任务。")
        elif isinstance(event, AgentEndEvent):
            self.finish_response()
            elapsed = time.monotonic() - self._started if self._started else 0
            label = {"stop": "完成", "aborted": "已取消", "length": "回复被截断",
                     "max_turns": "达到轮数上限", "error": "运行错误"}[event.reason]
            style = "success" if event.reason == "stop" else ("accent" if event.reason == "length" else "error")
            line = Text(f"  {label} · {elapsed:.1f}s", style=style)
            if self._input_tokens or self._output_tokens:
                line.append(f" · 输入 {self._input_tokens:,} / 输出 {self._output_tokens:,} tokens", style="muted")
            self.rich.print(line)
            self.rich.print()
            if event.error_message:
                self.note(event.error_message)
            elif event.reason == "length":
                self.note("模型达到本次生成长度限制。可提高 --max-output-tokens，或将较大文件分批写入。")
            self.phase = "就绪"
        self._refresh()

    def _call_summary(self, call: ToolCall) -> str:
        parts: list[str] = []
        for key in ("path", "command", "pattern"):
            value = call.arguments.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
        return " ".join(clean_text(" · ".join(parts)).split())[:160]

    def _tool_result(self, event: ToolExecutionEndEvent) -> None:
        call, message = event.call, event.result.message
        started = self._tool_started.pop(call.id, time.monotonic())
        elapsed = time.monotonic() - started
        text = message_text(message)
        if not self.verbose and not message.is_error:
            lines = text.splitlines()
            preview = "\n".join(lines[:8])
            omitted = len(lines) > 8 or len(preview) > 1800
            text = preview[:1800]
            if omitted:
                text += "\n… 输出展示已折叠；使用 --verbose 启动可查看完整返回。"
        language = "text"
        if call.name == "edit":
            language = "diff"
        elif call.name == "read":
            path = call.arguments.get("path")
            if isinstance(path, str):
                language = {".py": "python", ".ts": "typescript", ".js": "javascript",
                            ".json": "json", ".toml": "toml", ".yaml": "yaml",
                            ".yml": "yaml", ".md": "markdown", ".java": "java"}.get(Path(path).suffix, "text")
        body = Syntax(text or "(无文字输出)", language, theme="monokai", word_wrap=True, background_color="default")
        status = "失败" if message.is_error else "完成"
        self.rich.print(Padding(Panel(
            body, title=Text(f"{call.name} · {status} · {elapsed:.1f}s"),
            title_align="left", border_style="error" if message.is_error else BORDER,
            padding=(0, 1),
        ), (0, 1)))
        for block in message.content:
            if not isinstance(block, TextContent):
                self.note(f"图片附件  {block.mime_type}")

    def status(self, session: CodingSession) -> None:
        manager = session.session_manager
        estimate = session.compaction.last_estimate if session.compaction is not None else None
        table = Table.grid(padding=(0, 2))
        for label, value in (
            ("状态", self.phase), ("模型", self.model_name),
            ("模型输入", "文字、图片" if self.supports_images else "文字"),
            ("单次输出上限", f"{self.max_output_tokens:,} tokens" if self.max_output_tokens is not None else "由调用方配置"),
            ("轮次", str(session.agent.state.turn)),
            ("排队消息", "有" if session.has_queued_messages() else "无"),
            ("上下文", f"约 {estimate:,} / 1,000,000 tokens（上次检查）" if estimate is not None else "尚未估算"),
            ("当前节点", manager.leaf_id or "无"), ("会话文件", str(manager.file_path)),
        ):
            table.add_row(Text(label, style="muted"), Text(clean_text(value)))
        self.rich.print(Panel(table, title="会话状态", border_style=BORDER, title_align="left"))

    def history(self, session: CodingSession, limit: int | None = None) -> None:
        messages = [m for m in session.agent.state.messages if m.role in ("user", "assistant")]
        if limit is not None:
            messages = messages[-limit:]
        for message in messages:
            text = message_text(message)
            if not text:
                continue
            if message.role == "assistant":
                self.rich.print(Padding(Group(Text("mini-pi", style="accent"), markdown(text[:6000])), (1, 1)))
            else:
                self.rich.print(Panel(Text(text[:6000]), title="你 / 历史背景", border_style=BORDER, title_align="left"))
            if len(text) > 6000:
                self.note("此消息展示已截断，完整内容保存在会话中。")

    def tree(self, session: CodingSession) -> None:
        manager = session.session_manager
        table = Table(box=None, padding=(0, 1), expand=True)
        for column in ("", "节点", "父节点", "内容"):
            table.add_column(column, style="muted" if column == "父节点" else "")
        for entry in manager.get_entries():
            marker = ">" if entry.id == manager.leaf_id else ""
            parent = entry.parent_id[:12] if entry.parent_id else "root"
            if isinstance(entry, SessionMessageEntry):
                label = f"{entry.message.role}: {' '.join(message_text(entry.message).split())[:90]}"
            elif isinstance(entry, CompactionEntry):
                label = f"压缩: {entry.tokens_before:,} → {entry.tokens_after:,} tokens"
            else:
                label = f"模型: {entry.provider}/{entry.model_id}"
            table.add_row(marker, entry.id[:12], parent, Text(label))
        self.rich.print(Panel(table, title="会话分支", border_style=BORDER, title_align="left"))
