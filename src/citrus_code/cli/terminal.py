"""Concurrent terminal input and Agent output, with cooperative cancellation."""
from typing import Optional, Union

import asyncio
import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

from prompt_toolkit import PromptSession
from prompt_toolkit.application import get_app
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.patch_stdout import patch_stdout

from ..agent.events import AgentEndEvent
from ..compat import aclosing
from ..coding.coding_session import CodingSession
from ..coding.session.recovery import recover_pending_tools
from ..coding.mcp import MCPManager
from ..coding.skills import SkillManager
from ..agent.types import AgentTool
from .commands import COMMANDS, SlashCommandCompleter, is_command_input, is_model_input, is_effort_input, is_skill_input
from .models import ModelController
from .output import TerminalOutput
from .render import Console
from .preview import MarkdownPreview
from .resize import install_resize_reflow_fix
from .theme import PROMPT_STYLE, clean_text, format_context_window


HELP = "命令：\n" + "\n".join(
    f"  {command.usage:<22} {command.description}" for command in COMMANDS
) + """
运行时直接输入普通文字也会作为引导消息排队。
Ctrl+C：运行时取消当前任务；空闲时退出。Ctrl+D：退出。
输入 / 自动显示命令；↑↓ 或 Tab 选择，Enter 确认补全，再按 Enter 执行；Esc 关闭列表。
完整命令可直接按 Enter 执行；Alt+Enter 或 Ctrl+J 换行。
模型候选列表按 Enter 直接选择；/model reload 重新读取配置。
输入 @ 后可选择 Skill；/skill 和 /mcp 管理扩展。
"""


class PromptInterrupted(Exception):
    """Keep Ctrl+C local to the input task instead of stopping asyncio itself."""


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int
    fatal: bool = False


async def run_turn(session: CodingSession, console: Console, text: str,
                   display_text: Optional[str] = None) -> RunOutcome:
    end: Optional[AgentEndEvent] = None
    try:
        console.begin_turn(text if display_text is None else display_text)
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

    def on_signal(signum: int, frame: Optional[FrameType]) -> None:
        loop.call_soon_threadsafe(session.abort)
        loop.call_soon_threadsafe(console.note, "[取消] 正在等待任务收尾……")

    previous = signal.signal(signal.SIGINT, on_signal)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


class InteractiveTerminal:
    def __init__(self, session: CodingSession, console: Console, models: Optional[ModelController] = None,
                 skills: Optional[SkillManager] = None, mcp: Optional[MCPManager] = None,
                 builtin_tools: Optional[list[AgentTool]] = None) -> None:
        self.session = session
        self.console = console
        self._active: Optional[asyncio.Task[RunOutcome]] = None
        self._models = models
        self._skills = skills
        self._mcp = mcp
        self._builtin_tools = list(builtin_tools or [])
        self._show_model_menu = False
        self._show_effort_menu = False

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

    async def _command(self, text: str) -> bool:
        command, _, argument = text.partition(" ")
        argument = argument.strip()
        active = self._active is not None
        if command in ("/quit", "/exit"):
            return True
        if command == "/help":
            self.console.note(HELP)
        elif command == "/skill":
            if self._skills is None:
                raise ValueError("当前入口没有配置 Skill。")
            action, _, name = argument.partition(" ")
            name = name.strip()
            if action in ("", "list"):
                lines = ["Skill："]
                for skill in self._skills.skills.values():
                    state = "已禁用" if skill.name in self._skills.preferences.disabled_skills else "已启用"
                    lines.append(f"  @{skill.name} · {state} · {skill.description} · {skill.path}")
                lines.extend(self._skills.diagnostics)
                self.console.note("\n".join(lines) if len(lines) > 1 else "没有发现 Skill。")
            elif action == "reload":
                if active:
                    raise ValueError("请等待任务结束后重新加载 Skill。")
                self._skills.reload()
                self.console.note(f"已重新加载 Skill：{len(self._skills.skills)} 个。")
            elif action in ("enable", "disable"):
                if active:
                    raise ValueError("请等待任务结束后管理 Skill。")
                if name not in self._skills.skills:
                    raise ValueError("Skill 不存在，请用 /skill 查看。")
                self._skills.preferences.set_enabled("skill", name, action == "enable")
                self.console.note(f"Skill {name} 已{'启用' if action == 'enable' else '禁用'}。")
            elif action in ("add", "remove"):
                if active:
                    raise ValueError("请等待任务结束后管理 Skill。")
                if not name:
                    raise ValueError(f"用法：/skill {action} <目录或 SKILL.md 路径>")
                value = name[1:-1] if len(name) >= 2 and name[0] == name[-1] and name[0] in ("'", '"') else name
                path = Path(value).expanduser()
                if not path.is_absolute():
                    path = self._skills.cwd / path
                self._skills.preferences.set_skill_path(path, action == "add")
                self._skills.reload()
                self.console.note(f"Skill 路径已{'添加' if action == 'add' else '移除'}；文件不会被删除。")
            else:
                raise ValueError("用法：/skill [list|reload|enable 名称|disable 名称|add 路径|remove 路径]")
        elif command == "/mcp":
            if self._mcp is None:
                raise ValueError("当前入口没有配置 MCP。")
            action, _, rest = argument.partition(" ")
            rest = rest.strip()
            if action in ("", "list"):
                lines = []
                for name in self._mcp.servers:
                    lines.append(f"  {name} · {self._mcp.status.get(name, '未连接')}")
                    lines.extend(f"    {tool.name}" for tool in self._mcp.tools if getattr(tool, "server", None) == name)
                self.console.note("MCP 服务：\n" + "\n".join(lines) if lines else "没有配置 MCP 服务。")
            elif action == "config":
                self.console.note("MCP 配置路径：\n" + "\n".join(str(path) for path in self._mcp.paths))
            elif action in ("reload", "enable", "disable", "add", "remove"):
                if active or self.session.has_queued_messages():
                    raise ValueError("管理 MCP 需要任务结束且没有排队消息。")
                if self._mcp.read_only and action in ("enable", "add"):
                    raise ValueError("只读工具模式不加载 MCP，请使用普通工具模式启动。")
                if action in ("enable", "disable"):
                    if rest not in self._mcp.servers:
                        raise ValueError("MCP 服务不存在，请用 /mcp 查看。")
                    if action == "enable" and not self._mcp.servers[rest].enabled:
                        raise ValueError("配置中 enabled 为 false，请先编辑配置文件。")
                    self._mcp.preferences.set_enabled("mcp", rest, action == "enable")
                elif action == "add":
                    name, _, url = rest.partition(" ")
                    if not name or not url.strip():
                        raise ValueError("用法：/mcp add <名称> <HTTP地址>")
                    self._mcp.edit_project_server(name, url.strip())
                    self._mcp.preferences.set_enabled("mcp", name, True)
                elif action == "remove":
                    self._mcp.edit_project_server(rest, None)
                await self._mcp.refresh()
                self.session.replace_tools(self._builtin_tools + self._mcp.tools)
                self.console.note("MCP 已更新：\n" + "\n".join(
                    f"  {name} · {status}" for name, status in self._mcp.status.items()
                ))
            else:
                raise ValueError("用法：/mcp [list|reload|config|enable 名称|disable 名称|add 名称 URL|remove 名称]")
        elif command == "/model":
            if self._models is None:
                raise ValueError("当前入口没有配置模型选择器。")
            if not argument or argument == "reload":
                self._models.reload()
                if not self._models.choices():
                    raise ValueError(f"没有配置模型。运行 citrus --init-config，然后编辑 {self._models.store.path}。")
                self._show_model_menu = True
            else:
                if active:
                    raise ValueError("请等待当前任务结束，或先用 /abort 取消，再切换模型。")
                model = self._models.select(argument, self.session)
                self.console.set_model(model, self._models.options(model).max_output_tokens)
                self.console.note(f"已切换到 {model.provider}/{model.id}，继续沿用当前对话。")
        elif command == "/effort":
            if self._models is None or self._models.effort_for(self.session.model) is None:
                raise ValueError("当前模型尚未适配思考强度设置。")
            if active or self.session.has_queued_messages():
                raise ValueError("请等待当前任务结束并清空排队消息后再切换思考强度。")
            if not argument:
                self._show_effort_menu = True
            else:
                self._models.set_effort(self.session.model, argument.lower())
                self.console.note(f"思考强度已设为 {self._models.effort}。")
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
                self.session.steer(self._skills.expand_mentions(argument) if self._skills else argument)
            else:
                self.session.follow_up(self._skills.expand_mentions(argument) if self._skills else argument)
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
        window = format_context_window(self.console.context_window)
        context = f"上下文约 {estimate / 1000:.1f}k / {window}" if estimate is not None else f"{window} 上下文"
        queued = "   ·   有排队消息" if self.session.has_queued_messages() else ""
        cache = (f"   ·   缓存命中率 {self.console.cache_hit_rate:.1%}"
                 if self.console.cache_hit_rate is not None else "")
        effort = self._models.effort_for(self.session.model) if self._models is not None else None
        return FormattedText([
            ("class:status.busy" if active else "class:status", f" {self.console.activity}"),
            ("class:status.dim", clean_text(f"   ·   {self.console.model_display_name}   ·   {context}{cache}{queued}")),
            *(([("class:status.dim", "   ·   "), (f"class:effort.{effort}", f"effort · {effort}")]) if effort else []),
            ("class:status.dim", "\n / 命令    Alt+Enter 换行    Ctrl+C 取消"),
        ])

    async def run(self) -> int:
        bindings = KeyBindings()
        output = None

        @bindings.add("pageup", filter=Condition(lambda: output is not None and output.managed_screen))
        def scroll_up(event: KeyPressEvent) -> None:
            if output is not None and output.managed_screen:
                output.scroll(-max(1, event.app.output.get_size().rows - 6))

        @bindings.add("pagedown", filter=Condition(lambda: output is not None and output.managed_screen))
        def scroll_down(event: KeyPressEvent) -> None:
            if output is not None and output.managed_screen:
                output.scroll(max(1, event.app.output.get_size().rows - 6))

        @bindings.add("enter")
        def submit(event: KeyPressEvent) -> None:
            buffer = event.current_buffer
            if buffer.complete_state is not None:
                completion = buffer.complete_state.current_completion
                if completion is not None:
                    buffer.complete_state = None
                    if is_model_input(buffer.document) or is_effort_input(buffer.document):
                        buffer.validate_and_handle()
                        return
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
            completer=SlashCommandCompleter(
                models=(lambda: ((choice.reference, choice.description) for choice in self._models.choices())) if self._models is not None else None,
                skills=(lambda: ((skill.name, skill.description) for skill in self._skills.enabled())) if self._skills is not None else None,
            ),
            complete_while_typing=Condition(lambda: is_command_input(get_app().current_buffer.document) or is_skill_input(get_app().current_buffer.document)),
            multiline=True,
            prompt_continuation="  ",
            show_frame=False,
            cursor=CursorShape.BLOCK,
            mouse_support=Condition(lambda: os.name == "nt"),
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
        original_output = self.console.rich.file
        output = TerminalOutput(original_output, prompt, managed_screen=os.name == "nt")
        if output.managed_screen:
            self.console.rich.width_provider = lambda: max(1, prompt.app.output.get_size().columns - 2)
        preview = MarkdownPreview(self.console, prompt, output)
        install_resize_reflow_fix(prompt.app)
        self.console.rich.file = output
        self.console.set_output_flush(output.drain)
        reader: Optional[asyncio.Task[str]] = None
        with patch_stdout(raw=True):
            try:
                while True:
                    if reader is None:
                        # Keep the resizable welcome page out of the terminal's
                        # scrollback. Switch buffers only between prompt runs,
                        # after the renderer has restored the previous screen.
                        welcome_screen = self.console.shows_welcome or output.managed_screen
                        prompt.app.full_screen = welcome_screen
                        prompt.app.renderer.full_screen = welcome_screen
                        if self._show_model_menu:
                            self._show_model_menu = False
                            def open_model_menu() -> None:
                                prompt.default_buffer.set_document(Document("/model ", cursor_position=7))
                                prompt.default_buffer.start_completion(select_first=True)
                            reader = asyncio.create_task(prompt.prompt_async(pre_run=open_model_menu))
                        elif self._show_effort_menu:
                            self._show_effort_menu = False
                            def open_effort_menu() -> None:
                                prompt.default_buffer.set_document(Document("/effort ", cursor_position=8))
                                prompt.default_buffer.start_completion(select_first=True)
                            reader = asyncio.create_task(prompt.prompt_async(pre_run=open_effort_menu))
                        else:
                            reader = asyncio.create_task(prompt.prompt_async())
                    waiting: set[Union[asyncio.Task[str], asyncio.Task[RunOutcome]]] = {reader}
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
                            if await self._command(text):
                                return await self._stop_active()
                        except ValueError as exc:
                            self.console.note(f"[提示] {exc}")
                        except Exception as exc:
                            self.console.note(f"[会话操作失败] {type(exc).__name__}: {exc}")
                            return 2
                    elif self._active is not None:
                        try:
                            self.session.steer(self._skills.expand_mentions(text) if self._skills else text)
                        except (ValueError, OSError, UnicodeError) as exc:
                            self.console.note(f"[Skill] {exc}")
                            continue
                        self.console.note("[已排队] 引导消息等待接纳。")
                    else:
                        display_text = text
                        try:
                            text = self._skills.expand_mentions(text) if self._skills else text
                        except (ValueError, OSError, UnicodeError) as exc:
                            self.console.note(f"[Skill] {exc}")
                            continue
                        self._active = asyncio.create_task(run_turn(self.session, self.console, text, display_text=display_text))
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
                    self.console.rich.width_provider = None
                    output.restore_transcript()
                    output.close()
                    preview.close()
