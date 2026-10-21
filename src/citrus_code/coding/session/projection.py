"""Reconstruct model-visible messages without removing original tree nodes."""

from ...ai.types import SystemMessage, UserMessage
from ..history import get_pending_tool_calls
from .types import CompactionEntry, ContextMessageEntry, SessionEntry, SessionMessageEntry


def summary_message(entry: CompactionEntry) -> UserMessage:
    return UserMessage(
        content=(
            "[历史摘要：以下是较早对话的概括，作为背景资料使用，"
            "不代表新的用户指令。工具操作以实际结果为准。]\n\n"
            + entry.summary
        ),
        timestamp=entry.timestamp,
    )


def project_context_entries(branch: list[SessionEntry]) -> list[ContextMessageEntry]:
    active: list[ContextMessageEntry] = []
    for entry in branch:
        if isinstance(entry, SessionMessageEntry):
            active.append(ContextMessageEntry(
                id=entry.id, message=entry.message, task_start=entry.task_start,
            ))
        elif isinstance(entry, CompactionEntry):
            if not entry.summary.strip() or entry.tokens_after >= entry.tokens_before:
                raise ValueError("Compaction requires a nonempty summary and a smaller context")
            selected_ids = set(entry.retained_entry_ids)
            retained = [item for item in active if item.id in selected_ids]
            if [item.id for item in retained] != entry.retained_entry_ids:
                raise ValueError("Compaction references must be unique active messages in order")
            if any(item.is_summary for item in retained):
                raise ValueError("The previous summary must be merged, not retained separately")
            if any(
                isinstance(item.message, SystemMessage) and item.id not in selected_ids
                for item in active
            ):
                raise ValueError("Compaction must retain system messages")
            if get_pending_tool_calls([item.message for item in active]):
                raise ValueError("Cannot compact unfinished tool calls")
            systems = [item for item in retained if isinstance(item.message, SystemMessage)]
            conversation = [item for item in retained if not isinstance(item.message, SystemMessage)]
            projected = systems + [ContextMessageEntry(
                id=entry.id, message=summary_message(entry), is_summary=True,
            )] + conversation
            if get_pending_tool_calls([item.message for item in projected]):
                raise ValueError("Compaction retained an incomplete tool interaction")
            active = projected
    return active
