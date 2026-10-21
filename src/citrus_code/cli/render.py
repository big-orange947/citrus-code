"""Rich transcript rendering; the terminal owns interactive cursor movement."""

import time
from collections.abc import Awaitable, Callable
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
from ..ai.events import DoneEvent, ErrorEvent, RetryEvent, StartEvent, TextDeltaEvent, TextStartEvent, ToolCallStartEvent
from ..ai.types import Message, ModelSpec, TextContent, ToolCall
from ..coding.coding_session import CodingSession
from ..coding.session import CompactionEntry, SessionMessageEntry
from .theme import BORDER, RICH_THEME, clean_text
from .welcome import build_welcome


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
        self._output_flush: Callable[[], Awaitable[None]] | None = None
        self._live: Live | None = None
        self._started = 0.0
        self._busy = False
        self._tool_started: dict[str, float] = {}
        self._input_tokens = 0
        self._output_tokens = 0
        self._welcome_context: tuple[Path, str, bool] | None = None
        self._welcome_visible = False

    @property
    def response(self) -> str:
        return self._response

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def shows_welcome(self) -> bool:
        return self._welcome_visible

    def dismiss_welcome(self) -> None:
        self._welcome_visible = False
        self._refresh()

    def welcome_ansi(self, width: int, *, compact: bool = False) -> str:
        if self._welcome_context is None:
            return ""
        cwd, model_id, resumed = self._welcome_context
        buffer = StringIO()
        renderer = RichConsole(
            file=buffer, width=width, force_terminal=True, legacy_windows=False,
            color_system="truecolor", theme=RICH_THEME, markup=False,
            highlight=False, emoji=False,
        )
        if compact:
            renderer.print(Text(
                f"Citrus Code · {clean_text(model_id)} · 1M",
                style="accent", no_wrap=True, overflow="ellipsis",
            ))
        else:
            renderer.print(build_welcome(cwd, model_id, resumed=resumed, width=width))
        return buffer.getvalue().rstrip("\n")

    @property
    def activity(self) -> str:
        if not self._busy:
            return self.phase
        elapsed = max(0.0, time.monotonic() - self._started)
        frame = ("|", "/", "-", "\\")[int(elapsed / 0.12) % 4]
        return f"{frame} {self.phase} · {elapsed:.1f}s"

    def end_turn(self) -> None:
        self._busy = False
        self.phase = "就绪"
        self._refresh()

    def set_preview_listener(self, listener: Callable[[], None] | None) -> None:
        self._preview_listener = listener

    def set_output_flush(self, callback: Callable[[], Awaitable[None]] | None) -> None:
        self._output_flush = callback

    async def flush_output(self) -> None:
        if self._output_flush is not None:
            await self._output_flush()

    def _live_content(self) -> Group:
        activity = Text("  " + self.activity, style="accent")
        if self._response:
            return Group(Text("Citrus Code", style="accent"), markdown(self._response), activity)
        return Group(activity)

    def _refresh(self) -> None:
        if self._preview_listener is not None:
            self._preview_listener()
        elif (self._busy or self._response) and self.rich.is_terminal and not self.rich.is_dumb_terminal:
            if self._live is None:
                self._live = Live(
                    get_renderable=self._live_content,
                    console=self.rich, transient=True, refresh_per_second=8,
                    redirect_stdout=False, redirect_stderr=False,
                )
                self._live.start(refresh=True)
        elif self._live is not None:
            self._live.stop()
            self._live = None

    def finish_response(self, text: str | None = None) -> None:
        content = self._response if text is None else text
        self._response = ""
        if self._live is not None:
            self._live.stop()
            self._live = None
        if content.strip():
            self.rich.print(Padding(Group(
                Text("Citrus Code", style="accent"), markdown(content),
            ), (1, 1, 0, 1)))
        self._refresh()

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
        self, cwd: Path, model: ModelSpec, *, fake: bool, resumed: bool, interactive: bool,
    ) -> None:
        self.model_name = model.id
        self.supports_images = model.supports_images
        if interactive:
            # The prompt owns this panel so resize can repaint it, rather than
            # leaving a permanently printed border for the terminal to reflow.
            self._welcome_context = (cwd, model.id, resumed)
            self._welcome_visible = True
        else:
            self.rich.print(Text.assemble(
                ("Citrus Code", "accent"), f"  {model.id} · 1M  ", (str(cwd), "muted"),
            ))
        if fake:
            self.note("[离线模式] 只输出预设文字，不执行编程任务。")

    def begin_turn(self, text: str) -> None:
        self.dismiss_welcome()
        self.finish_response()
        self._started = time.monotonic()
        self._busy = True
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
            elif isinstance(model_event, RetryEvent):
                # The failed attempt is never committed. Discard its preview
                # instead of printing text that will be regenerated.
                self._response = ""
                self.phase = f"等待重试 {model_event.attempt}/{model_event.max_retries}"
                self.rich.print(Text(
                    f"  模型请求暂时失败，{model_event.delay_seconds:g}s 后自动重试 "
                    f"({model_event.attempt}/{model_event.max_retries})",
                    style="accent",
                ))
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
            self.end_turn()
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
            elif event.reason == "error":
                self.note("运行失败，但未收到具体错误详情。请检查模型连接；已经完成的工具结果仍保留在会话中。")
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
                self.rich.print(Padding(Group(Text("Citrus Code", style="accent"), markdown(text[:6000])), (1, 1)))
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
