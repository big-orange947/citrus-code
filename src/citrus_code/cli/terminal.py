"""Concurrent terminal input and Agent output, with cooperative cancellation."""

import asyncio
import os
import signal
from collections.abc import Iterator
from contextlib import aclosing, contextmanager
from dataclasses import dataclass
from types import FrameType

from prompt_toolkit import PromptSession
from prompt_toolkit.application import get_app
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.patch_stdout import patch_stdout

from ..agent.events import AgentEndEvent
from ..coding.coding_session import CodingSession
from ..coding.session.recovery import recover_pending_tools
from .commands import COMMANDS, SlashCommandCompleter, is_command_input
from .output import TerminalOutput
from .render import Console
from .preview import MarkdownPreview
from .theme import PROMPT_STYLE, clean_text


HELP = "命令：\n" + "\n".join(
    f"  {command.usage:<22} {command.description}" for command in COMMANDS
) + """
运行时直接输入普通文字也会作为引导消息排队。
Ctrl+C：运行时取消当前任务；空闲时退出。Ctrl+D：退出。
输入 / 自动显示命令；↑↓ 或 Tab 选择，Enter 确认补全，再按 Enter 执行；Esc 关闭列表。
完整命令可直接按 Enter 执行；Alt+Enter 或 Ctrl+J 换行。
"""


class PromptInterrupted(Exception):
    """Keep Ctrl+C local to the input task instead of stopping asyncio itself."""


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int
    fatal: bool = False


async def run_turn(session: CodingSession, console: Console, text: str) -> RunOutcome:
    end: AgentEndEvent | None = None
    try:
        console.begin_turn(text)
        await console.flush_output()
        async with aclosing(session.prompt(text)) as stream:
            async for event in stream:
                console.event(event)
                await console.flush_output()
                if isinstance(event, AgentEndEvent):
                    end = event
        if end is None:
            raise RuntimeError("Agent 事件流结束，但未收到结束事件")
        if end.reason == "aborted":
            return RunOutcome(130)
        return RunOutcome(0 if end.reason == "stop" else 1)
    except Exception as exc:
        console.finish_response()
        console.note(f"[运行失败] {type(exc).__name__}: {exc}")
        console.note("本次 CLI 将退出。请检查会话文件后使用 --resume 重新加载。")
        await console.flush_output()
        return RunOutcome(2, fatal=True)
    finally:
        console.end_turn()
        console.finish_response()
        await console.flush_output()


@contextmanager
def cancel_on_sigint(session: CodingSession, console: Console) -> Iterator[None]:
    loop = asyncio.get_running_loop()

    def on_signal(signum: int, frame: FrameType | None) -> None:
        loop.call_soon_threadsafe(session.abort)
        loop.call_soon_threadsafe(console.note, "[取消] 正在等待任务收尾……")

    previous = signal.signal(signal.SIGINT, on_signal)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


class InteractiveTerminal:
    def __init__(self, session: CodingSession, console: Console) -> None:
        self.session = session
        self.console = console
        self._active: asyncio.Task[RunOutcome] | None = None

    async def _stop_active(self) -> int:
        task = self._active
        if task is None:
            return 0
        if not task.done():
            if self.session.is_running:
                self.session.abort()
            else:
                # A newly scheduled turn may not have entered Agent.prompt yet.
                task.cancel()
        results = await asyncio.gather(task, return_exceptions=True)
        self._active = None
        result = results[0]
        if isinstance(result, RunOutcome):
            return result.exit_code if result.fatal else 0
        if not isinstance(result, asyncio.CancelledError):
            self.console.note(f"[任务退出失败] {result}")
            await self.console.flush_output()
            return 2
        return 0

    def _command(self, text: str) -> bool:
        command, _, argument = text.partition(" ")
        argument = argument.strip()
        active = self._active is not None
        if command in ("/quit", "/exit"):
            return True
        if command == "/help":
            self.console.note(HELP)
        elif command == "/abort":
            self.session.abort()
            self.console.note("[取消] 正在等待任务收尾……" if active else "当前没有运行中的任务。")
        elif command == "/status":
            self.console.status(self.session)
        elif command == "/history":
            self.console.history(self.session)
        elif command == "/tree":
            self.console.tree(self.session)
        elif command in ("/steer", "/follow"):
            if not active:
                raise ValueError("当前没有运行中的任务，请直接输入新问题。")
            if not argument:
                raise ValueError(f"用法：{command} <消息>")
            if command == "/steer":
                self.session.steer(argument)
            else:
                self.session.follow_up(argument)
            self.console.note("[已排队] 消息将在运行时接纳后保存。")
        elif command == "/clear-queue":
            self.session.agent.clear_all_queues()
            self.console.note("已清空尚未接纳的排队消息。")
        elif command == "/checkout":
            if not argument:
                raise ValueError("用法：/checkout <节点ID或唯一前缀>")
            candidates = [
                entry.id for entry in self.session.session_manager.get_entries()
                if entry.id.startswith(argument)
            ]
            if len(candidates) != 1:
                raise ValueError("节点不存在或前缀不唯一，请用 /tree 查看。")
            self.session.checkout(candidates[0])
            self.console.note(f"已切换到 {candidates[0]}。项目文件仍保持当前磁盘状态。")
        elif command == "/recover":
            if active or self.session.has_queued_messages():
                raise ValueError("恢复需要空闲且没有排队消息。")
            count = recover_pending_tools(self.session.session_manager)
            self.session.agent.replace_messages(
                self.session.session_manager.build_context().messages
            )
            self.console.note(f"已补齐 {count} 条执行状态未知的工具结果。")
        else:
            raise ValueError("未知命令。输入 /help 查看帮助。")
        return False

    def _toolbar(self) -> FormattedText:
        active = self._active is not None
        estimate = self.session.compaction.last_estimate if self.session.compaction is not None else None
        context = f"上下文约 {estimate / 1000:.1f}k / 1M" if estimate is not None else "1M 上下文"
        queued = " · 有排队消息" if self.session.has_queued_messages() else ""
        return FormattedText([
            ("class:status.busy" if active else "class:status", f" {self.console.activity}"),
            ("class:status.dim", clean_text(f" · {self.console.model_name} · {context}{queued}")),
            ("class:status.dim", "\n / 命令  ↑↓ 选择  Tab 补全  Enter 确认/发送  Alt+Enter 换行  Ctrl+C 取消"),
        ])

    async def run(self) -> int:
        bindings = KeyBindings()

        @bindings.add("enter")
        def submit(event: KeyPressEvent) -> None:
            buffer = event.current_buffer
            if buffer.complete_state is not None:
                completion = buffer.complete_state.current_completion
                if completion is not None:
                    buffer.complete_state = None
                    if any(command.name == completion.text and command.argument for command in COMMANDS):
                        buffer.insert_text(" ")
                    return
                elif buffer.complete_state.completions:
                    completion = buffer.complete_state.completions[0]
                    if buffer.text != completion.text:
                        buffer.apply_completion(completion)
                        if any(command.name == completion.text and command.argument for command in COMMANDS):
                            buffer.insert_text(" ")
                        return
                    buffer.complete_state = None
                if completion is not None and any(
                    command.name == completion.text and command.argument for command in COMMANDS
                ):
                    buffer.insert_text(" ")
                    return
            buffer.validate_and_handle()

        @bindings.add("escape", filter=has_completions)
        def dismiss_completions(event: KeyPressEvent) -> None:
            event.current_buffer.cancel_completion()

        @bindings.add("escape", "enter")
        @bindings.add("c-j")
        def newline(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text("\n")

        prompt = PromptSession[str](
            message=FormattedText([("class:prompt", "› ")]),
            interrupt_exception=PromptInterrupted,
            completer=SlashCommandCompleter(),
            complete_while_typing=Condition(lambda: is_command_input(get_app().current_buffer.document)),
            multiline=True,
            prompt_continuation="  ",
            show_frame=False,
            cursor=CursorShape.BLINKING_BLOCK,
            style=PROMPT_STYLE,
            color_depth=ColorDepth.DEPTH_1_BIT if "NO_COLOR" in os.environ else None,
            key_bindings=bindings,
            auto_suggest=AutoSuggestFromHistory(),
            bottom_toolbar=self._toolbar,
            placeholder=FormattedText([("class:placeholder", "描述任务，或输入 /help")]),
            reserve_space_for_menu=0,
            refresh_interval=0.12,
            erase_when_done=True,
        )
        prompt.app.min_redraw_interval = 0.08
        prompt.app.terminal_size_polling_interval = 0.12
        preview = MarkdownPreview(self.console, prompt)
        original_output = self.console.rich.file
        output = TerminalOutput(original_output, prompt)
        self.console.rich.file = output
        self.console.set_output_flush(output.drain)
        reader: asyncio.Task[str] | None = None
        with patch_stdout(raw=True):
            try:
                while True:
                    if reader is None:
                        # Keep the resizable welcome page out of the terminal's
                        # scrollback. Switch buffers only between prompt runs,
                        # after the renderer has restored the previous screen.
                        welcome_screen = self.console.shows_welcome
                        prompt.app.full_screen = welcome_screen
                        prompt.app.renderer.full_screen = welcome_screen
                        reader = asyncio.create_task(prompt.prompt_async())
                    waiting: set[asyncio.Task[str] | asyncio.Task[RunOutcome]] = {reader}
                    if self._active is not None:
                        waiting.add(self._active)
                    done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

                    if self._active is not None and self._active in done:
                        outcome = self._active.result()
                        self._active = None
                        prompt.app.invalidate()
                        if outcome.fatal:
                            return outcome.exit_code

                    if reader not in done:
                        continue
                    completed_reader = reader
                    reader = None
                    try:
                        text = completed_reader.result().strip()
                    except PromptInterrupted:
                        if self._active is None:
                            return 0
                        self.session.abort()
                        self.console.note("[取消] 正在等待任务收尾……")
                        await self.console.flush_output()
                        continue
                    except EOFError:
                        return await self._stop_active()

                    if not text:
                        continue
                    self.console.dismiss_welcome()
                    if text.startswith("/"):
                        try:
                            if self._command(text):
                                return await self._stop_active()
                        except ValueError as exc:
                            self.console.note(f"[提示] {exc}")
                        except Exception as exc:
                            self.console.note(f"[会话操作失败] {type(exc).__name__}: {exc}")
                            return 2
                    elif self._active is not None:
                        self.session.steer(text)
                        self.console.note("[已排队] 引导消息等待接纳。")
                    else:
                        self._active = asyncio.create_task(run_turn(self.session, self.console, text))
                        prompt.app.invalidate()
                    await self.console.flush_output()
            finally:
                if reader is not None:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
                try:
                    await self._stop_active()
                    await self.console.flush_output()
                finally:
                    self.console.set_output_flush(None)
                    self.console.rich.file = original_output
                    output.close()
                    preview.close()
