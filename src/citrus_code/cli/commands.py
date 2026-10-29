"""Slash command descriptions shared by help and input completion."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Optional
import re

from prompt_toolkit.completion import Completer, CompleteEvent, Completion
from prompt_toolkit.document import Document


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    argument: str = ""

    @property
    def usage(self) -> str:
        return f"{self.name} {self.argument}" if self.argument else self.name


COMMANDS = (
    Command("/help", "显示帮助"),
    Command("/status", "查看运行状态、模型和消息队列"),
    Command("/model", "选择模型；reload 重新读取配置文件", "[供应商/模型ID]"),
    Command("/effort", "设置思考强度", "[low|high|max]"),
    Command("/skill", "查看和管理 Skill", "[list|reload|enable|disable|add|remove] [参数]"),
    Command("/mcp", "查看或管理 MCP 服务", "[list|reload|enable|disable|add|remove|config] [参数]"),
    Command("/history", "显示当前分支的对话"),
    Command("/tree", "列出会话节点与当前分支"),
    Command("/checkout", "空闲且无排队消息时切换分支，支持唯一 ID 前缀", "<节点ID>"),
    Command("/steer", "向正在运行的 Agent 追加引导消息", "<消息>"),
    Command("/follow", "排队，等待 Agent 正常完成后处理", "<消息>"),
    Command("/clear-queue", "清空尚未接纳的排队消息"),
    Command("/recover", "空闲且无排队消息时补齐缺失的工具结果"),
    Command("/abort", "取消当前任务，等待收尾"),
    Command("/quit", "取消当前任务并退出"),
    Command("/exit", "退出，等同于 /quit"),
)


def is_command_input(document: Document) -> bool:
    return (
        document.text.startswith("/")
        and document.cursor_position == len(document.text)
        and (not any(character.isspace() for character in document.text) or is_model_input(document) or is_effort_input(document))
    )


def is_model_input(document: Document) -> bool:
    return (
        document.text.startswith("/model ")
        and document.cursor_position == len(document.text)
        and not any(character.isspace() for character in document.text[len("/model "):])
    )


def is_effort_input(document: Document) -> bool:
    return (
        document.text.startswith("/effort ")
        and document.cursor_position == len(document.text)
        and not any(character.isspace() for character in document.text[len("/effort "):])
    )


def is_skill_input(document: Document) -> bool:
    return re.search(r"(?:^|\s)@[a-z0-9-]*$", document.text_before_cursor) is not None


class SlashCommandCompleter(Completer):
    def __init__(self, models: Optional[Callable[[], Iterable[tuple[str, str]]]] = None,
                 skills: Optional[Callable[[], Iterable[tuple[str, str]]]] = None) -> None:
        self._models = models
        self._skills = skills

    def get_completions(
        self, document: Document, complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        if is_skill_input(document):
            match = re.search(r"@([a-z0-9-]*)$", document.text_before_cursor)
            if match is not None and self._skills is not None:
                prefix = match.group(1)
                for name, description in self._skills():
                    if name.startswith(prefix):
                        yield Completion("@" + name, start_position=-len(prefix)-1, display="@" + name, display_meta=description)
            return
        if not is_command_input(document):
            return
        if is_effort_input(document):
            prefix = document.text[len("/effort "):].lower()
            for level, description in (("low", "低"), ("high", "高"), ("max", "最高")):
                if level.startswith(prefix):
                    yield Completion(level, start_position=-len(prefix), display_meta=description)
            return
        if is_model_input(document):
            prefix = document.text[len("/model "):]
            if self._models is not None:
                for reference, description in self._models():
                    if prefix.lower() in reference.lower():
                        yield Completion(reference, start_position=-len(prefix), display=reference, display_meta=description)
            if "reload".startswith(prefix):
                yield Completion("reload", start_position=-len(prefix), display_meta="重新读取模型配置")
            return
        prefix = document.text
        for command in COMMANDS:
            if command.name.startswith(prefix):
                yield Completion(
                    command.name,
                    start_position=-len(prefix),
                    display=command.usage,
                    display_meta=command.description,
                )
