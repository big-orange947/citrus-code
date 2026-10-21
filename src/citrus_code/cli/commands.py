"""Slash command descriptions shared by help and input completion."""

from collections.abc import Iterable
from dataclasses import dataclass

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
        and not any(character.isspace() for character in document.text)
    )


class SlashCommandCompleter(Completer):
    def get_completions(
        self, document: Document, complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        if not is_command_input(document):
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
