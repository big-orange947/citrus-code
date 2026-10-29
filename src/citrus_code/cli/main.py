"""Parse CLI options and assemble providers, tools, storage, and terminal UI."""
from typing import Optional

import argparse
import asyncio
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..ai.providers.fake import FakeProvider
from ..coding.coding_session import CodingSession
from ..coding.project_context import load_project_context
from ..coding.session import SessionManager
from ..coding.session.recovery import recover_pending_tools
from ..coding.extensions import ExtensionPreferences
from ..coding.mcp import MCPManager
from ..coding.skills import SkillManager
from ..coding.tools import READ_ONLY_TOOLS, create_tools
from ..settings import SettingsStore
from .models import ModelController
from .prompts import build_system_prompt
from .render import Console
from .session_files import (
    LaunchSettings,
    ShellName,
    load_settings,
    save_settings,
    session_lease,
    session_paths,
)
from .terminal import InteractiveTerminal, cancel_on_sigint, run_turn


@dataclass(frozen=True)
class Options:
    prompt: Optional[str]
    cwd: Optional[Path]
    session_dir: Optional[Path]
    resume: Optional[str]
    sessions: bool
    fake: bool
    shell: Optional[ShellName]
    tools: Optional[str]
    model: Optional[str]
    max_turns: Optional[int]
    max_output_tokens: int
    model_timeout: int
    verbose: bool
    config: Optional[Path]
    init_config: bool
    provider: Optional[str]


def positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return number


def parse_options(argv: Optional[list[str]]) -> Options:
    parser = argparse.ArgumentParser(
        prog="citrus",
        description="Python coding agent；不传任务进入交互模式，传入任务则单次运行。",
    )
    parser.add_argument("prompt", nargs="?", help="单次任务；多词任务请用引号包裹")
    parser.add_argument("--cwd", type=Path, help="工作目录；恢复时默认使用会话原目录")
    parser.add_argument("--session-dir", type=Path, help="会话目录，默认 <工作目录>/.citrus-code/sessions")
    parser.add_argument("--resume", nargs="?", const="latest", metavar="FILE", help="恢复指定 JSONL 文件；省略路径时恢复当前会话目录中最近的文件")
    parser.add_argument("--sessions", action="store_true", help="列出当前会话目录的文件并退出")
    parser.add_argument("--fake", action="store_true", help="离线固定文字响应，不调用远程模型")
    parser.add_argument("--shell", choices=("bash", "powershell"), help="新会话的命令工具；Windows 默认 powershell，其他平台默认 bash")
    parser.add_argument("--tools", choices=("coding", "read-only", "all"), help="新会话的工具组合，默认 coding")
    parser.add_argument("--model", help="模型 ID；恢复时须与会话记录匹配")
    parser.add_argument("--provider", help="配置文件中的供应商 ID；也可直接使用 --model 供应商/模型ID")
    parser.add_argument("--config", type=Path, help="指定 JSON 模型配置文件，替代默认配置路径")
    parser.add_argument("--init-config", action="store_true", help="创建示例模型配置后退出；不覆盖已有文件")
    parser.add_argument("--max-turns", type=positive_integer, help="本次运行的模型调用轮数上限，默认不限制")
    parser.add_argument(
        "--max-output-tokens", type=positive_integer, default=32768,
        help="每次模型调用的输出上限，默认 32768；工具参数和文件正文也计入",
    )
    parser.add_argument(
        "--model-timeout", type=positive_integer, default=300,
        help="等待模型响应数据的最长空闲时间（秒），默认 300；不是整项任务的总时限",
    )
    parser.add_argument("--verbose", action="store_true", help="同时打印成功工具调用的完整返回文字")
    args = parser.parse_args(argv)
    if args.sessions and (args.prompt is not None or args.resume is not None):
        parser.error("--sessions 请单独使用，可搭配 --cwd 和 --session-dir")
    prompt = args.prompt
    if not args.sessions and not args.init_config and prompt is None:
        if not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
            if not prompt:
                parser.error("标准输入为空；请传入任务或在终端中启动交互模式")
        elif not sys.stdout.isatty():
            parser.error("交互模式需要终端输出；重定向输出时请传入单次任务")
    if prompt is not None and not prompt.strip():
        parser.error("任务不能为空")
    return Options(
        prompt, args.cwd, args.session_dir, args.resume, args.sessions, args.fake,
        args.shell, args.tools, args.model, args.max_turns, args.max_output_tokens,
        args.model_timeout, args.verbose, args.config, args.init_config, args.provider,
    )


def choose_tools(options: Options, default_shell: Optional[ShellName] = None) -> LaunchSettings:
    shell: ShellName = options.shell or default_shell or ("powershell" if os.name == "nt" else "bash")
    if shell == "powershell" and os.name != "nt":
        raise ValueError("当前内置 powershell 工具只支持 Windows")
    if options.tools == "read-only":
        names = list(READ_ONLY_TOOLS)
    elif options.tools == "all":
        names = ["read", "bash", "edit", "write", "grep", "find", "ls"]
        if os.name == "nt":
            names.append("powershell")
    else:
        names = ["read", shell, "edit", "write"]
    return LaunchSettings(shell=shell, tools=names)


def list_sessions(directory: Path) -> None:
    paths = session_paths(directory)
    print(f"会话目录：{directory}")
    if not paths:
        print("没有保存的会话。")
    for path in paths:
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        print(f"{modified}  {path}")


async def run_application(options: Options) -> int:
    launch_cwd = (options.cwd or Path.cwd()).expanduser().resolve()
    if not launch_cwd.is_dir():
        raise NotADirectoryError(f"工作目录不存在：{launch_cwd}")
    if options.init_config:
        store = SettingsStore(launch_cwd, options.config)
        try:
            path = store.initialize()
        except FileExistsError:
            print(f"配置已存在，保留原文件：{store.path}")
            return 0
        print(f"已创建配置：{path}\n请填写 api_key；运行 citrus 后可输入 /model 选择模型。")
        return 0
    directory = (
        options.session_dir.expanduser().resolve()
        if options.session_dir is not None
        else launch_cwd / ".citrus-code" / "sessions"
    )
    if options.sessions:
        list_sessions(directory)
        return 0

    console = Console(options.verbose, max_output_tokens=options.max_output_tokens)
    async with AsyncExitStack() as resources:
        if options.resume is not None:
            if options.resume == "latest":
                paths = session_paths(directory)
                if not paths:
                    raise FileNotFoundError(f"没有可以恢复的会话：{directory}")
                path = paths[0]
            else:
                path = Path(options.resume).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"会话文件不存在：{path}")
            resources.enter_context(session_lease(path))
            manager = SessionManager.load(path)
            cwd = Path(manager.header.cwd).expanduser().resolve()
            if options.cwd is not None and launch_cwd != cwd:
                raise ValueError(f"--cwd 与会话原工作目录不一致：{cwd}")
            if not cwd.is_dir():
                raise NotADirectoryError(f"会话原工作目录不存在：{cwd}")
            stored_settings = load_settings(path)
            if stored_settings is not None:
                requested = choose_tools(options, default_shell=stored_settings.shell)
                if options.shell is not None and requested.shell != stored_settings.shell:
                    raise ValueError("恢复时沿用原命令工具；更换工具请创建新会话")
                if options.tools is not None and requested.tools != stored_settings.tools:
                    raise ValueError("恢复时沿用原工具组合；更换工具请创建新会话")
                settings = stored_settings
            else:
                settings = choose_tools(options)
                console.note("[恢复] 此会话没有 CLI 工具配置，使用当前选择的工具组合。")
        else:
            cwd = launch_cwd
            manager = SessionManager(cwd)
            path = directory / f"{manager.header.id}.jsonl"
            settings = choose_tools(options)
            resources.enter_context(session_lease(path))

        context_files = load_project_context(cwd)
        extensions = ExtensionPreferences(cwd)
        skills = SkillManager(cwd, extensions)
        mcp = MCPManager(cwd, extensions, read_only=set(settings.tools) == set(READ_ONLY_TOOLS))
        resources.push_async_callback(mcp.close)
        await mcp.refresh()
        context = manager.build_context()
        fake = options.fake or context.provider == "fake"
        fake_provider = FakeProvider(
            chunks=["这是离线模拟回答。", "当前入口已连接事件流、", "会话保存和消息队列。",
                    "模拟模型只输出预设文字，", "不会执行你的编程任务。\n"],
            delay_seconds=0.25,
        )
        client = await resources.enter_async_context(httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=options.model_timeout, write=120.0, pool=30.0),
        ))
        models = ModelController(
            SettingsStore(cwd, options.config), client, cwd, settings.tools,
            context_files, options.max_output_tokens, fake_provider=fake_provider, skills=skills,
        )
        if fake:
            if context.provider not in (None, "fake"):
                raise ValueError("--fake 不能用于恢复真实模型会话")
            model = fake_provider.get_models()[0]
            if options.model is not None and options.model not in {model.id, "fake/" + model.id}:
                raise ValueError("离线模式的模型 ID 为 fake-text")
        else:
            reference = options.model or (f"{context.provider}/{context.model_id}" if context.provider else None)
            model = models.resolve(reference, provider_id=options.provider)
        provider = models.provider(model)

        if context.provider is not None and (
            context.provider != model.provider or context.model_id != model.id
        ):
            raise ValueError("恢复时先使用会话记录的模型；进入 CLI 后可通过 /model 切换。")
        builtin_tools = create_tools(cwd, settings.tools)
        tools = builtin_tools + mcp.tools
        system_prompt = build_system_prompt(
            cwd, settings.tools, context_files,
            model_id=f"{model.provider}/{model.id}", supports_images=model.supports_images,
        )
        if options.resume is None:
            manager.save(path)
            save_settings(path, settings)

        recovered = recover_pending_tools(manager)
        session = CodingSession(
            provider=provider, model=model, tools=tools, session_manager=manager,
            system_prompt=system_prompt,
            hooks=models.hooks(model),
            options=models.options(model),
            max_turns=options.max_turns,
        )
        console.set_model(model, models.options(model).max_output_tokens)
        console.welcome(
            cwd, model,
            fake=fake, resumed=options.resume is not None, interactive=options.prompt is None,
        )
        if recovered:
            console.note(f"[恢复] 为 {recovered} 个缺少结果的工具调用补充了执行状态未知记录。")
        for diagnostic in skills.diagnostics:
            console.note(f"[Skill] {diagnostic}")
        for name, status in mcp.status.items():
            if status.startswith("连接失败"):
                console.note(f"[MCP] {name}：{status}；可用 /mcp reload 重试。")
        if options.resume is not None and options.prompt is None:
            console.history(session, limit=4)
        if options.prompt is not None:
            with cancel_on_sigint(session, console):
                outcome = await run_turn(
                    session, console, skills.expand_mentions(options.prompt), display_text=options.prompt,
                )
            return outcome.exit_code
        return await InteractiveTerminal(
            session, console, models=models, skills=skills, mcp=mcp, builtin_tools=builtin_tools,
        ).run()


def main(argv: Optional[list[str]] = None) -> int:
    try:
        options = parse_options(argv)
        return asyncio.run(run_application(options))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"citrus: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
