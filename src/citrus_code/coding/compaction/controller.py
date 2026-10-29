"""Coding policy for automatic compaction and its pending durable checkpoint."""
from typing import Optional

import time
from uuid import uuid4

from ...agent.cancellation import AbortSignal
from ...agent.context import PreparedContext
from ...agent.events import ContextCompactedEvent
from ...ai.provider import Provider
from ...ai.types import (
    AssistantMessage, ModelContext, ModelSpec, SystemMessage,
    ToolResultMessage, UserMessage,
)
from ..history import get_pending_tool_calls
from ..session import CompactionEntry, ContextMessageEntry, SessionManager
from ..session.projection import project_context_entries
from .summarize import generate_summary
from .tokens import (
    COMPACTION_THRESHOLD, KEEP_RECENT_ROUNDS, KEEP_RECENT_TOKENS,
    estimate_message, estimate_request, replace_system_messages,
)


class CompactionController:
    def __init__(self, provider: Provider, model: ModelSpec, manager: SessionManager) -> None:
        self._provider = provider
        self._model = model
        self._manager = manager
        self._threshold = int(model.context_window * 0.6) if model.context_window is not None else COMPACTION_THRESHOLD
        self._recent_budget = min(KEEP_RECENT_TOKENS, max(1, self._threshold // 6))
        self._pending: Optional[CompactionEntry] = None
        self.last_estimate: Optional[int] = None

    async def prepare(self, context: ModelContext, signal: AbortSignal) -> Optional[PreparedContext]:
        self._pending = None
        branch = self._manager.get_branch()
        entries = project_context_entries(branch)
        ordinary = [item.message for item in entries if not isinstance(item.message, SystemMessage)]
        request_ordinary = [m for m in context.messages if not isinstance(m, SystemMessage)]
        # Custom hooks may change system instructions and tools, but compaction must
        # not silently persist arbitrary rewrites of conversation messages.
        if ordinary != request_ordinary:
            raise ValueError("Automatic compaction requires hooks to preserve conversation messages")
        latest = next(
            (entry for entry in reversed(branch) if isinstance(entry, CompactionEntry)), None,
        )
        tokens_before = self._estimate(context, entries, latest)
        self.last_estimate = tokens_before
        if tokens_before < self._threshold:
            return None
        if get_pending_tool_calls(ordinary):
            raise ValueError("Compaction requires all tool results to be present")

        retained_ids = select_retained_entries(entries, recent_budget=self._recent_budget)
        retained_set = set(retained_ids)
        removed = [item.message for item in entries if item.id not in retained_set]
        if not any(
            item.id not in retained_set and not item.is_summary
            and not isinstance(item.message, SystemMessage)
            for item in entries
        ):
            raise RuntimeError("Context exceeds 60%, but no older complete interaction can be compacted")

        retained_messages = [item.message for item in entries if item.id in retained_set]
        if estimate_request(replace_system_messages(retained_messages, context)) >= self._threshold:
            raise RuntimeError(
                "Current task requirements and recent interactions alone exceed 60% of the context; "
                "reduce the task input or oversized tool results"
            )

        parent_id = self._manager.leaf_id
        summary = await generate_summary(self._provider, self._model, removed, signal)
        signal.raise_if_aborted()
        if self._manager.leaf_id != parent_id:
            raise RuntimeError("Session changed during summary generation")
        entry = CompactionEntry(
            id=uuid4().hex, parent_id=parent_id, timestamp=int(time.time() * 1000),
            summary=summary, retained_entry_ids=retained_ids,
            tokens_before=tokens_before, tokens_after=0,
        )
        projected = project_context_entries(branch + [entry])
        messages = [item.message for item in projected]
        request = replace_system_messages(messages, context)
        tokens_after = estimate_request(request)
        if tokens_after >= min(tokens_before, self._threshold):
            raise RuntimeError("Summary did not reduce context below the compaction threshold")
        entry.tokens_after = tokens_after
        self._pending = entry
        # No persistence here: cancellation may still win the runtime's await race.
        # CodingSession commits only when it receives ContextCompactedEvent.
        return PreparedContext(messages, request, tokens_before, tokens_after)

    def commit(self, event: ContextCompactedEvent) -> str:
        entry = self._pending
        if entry is None:
            raise RuntimeError("No prepared compaction checkpoint")
        projected = project_context_entries(self._manager.get_branch() + [entry])
        if event.messages != [item.message for item in projected]:
            raise RuntimeError("Compaction event does not match its checkpoint")
        result = self._manager.append_compaction(entry)
        self._pending = None
        self.last_estimate = event.tokens_after
        return result

    def _estimate(
        self, context: ModelContext, entries: list[ContextMessageEntry],
        latest: Optional[CompactionEntry],
    ) -> int:
        stale_ids = set(latest.retained_entry_ids) if latest is not None else set()
        for index in range(len(entries) - 1, -1, -1):
            item = entries[index]
            message = item.message
            if not isinstance(message, AssistantMessage) or item.id in stale_ids:
                continue
            if (
                message.provider != self._model.provider or message.model != self._model.id
                or message.stop_reason in ("error", "aborted", "pending") or message.usage is None
            ):
                continue
            usage = message.usage
            baseline = max(usage.total_tokens, usage.input_tokens + usage.output_tokens)
            if baseline <= 0:
                continue
            trailing = sum(estimate_message(item.message) for item in entries[index + 1:])
            stored_system = sum(
                estimate_message(item.message) for item in entries if isinstance(item.message, SystemMessage)
            )
            current_system = sum(
                estimate_message(m) for m in context.messages if isinstance(m, SystemMessage)
            )
            # Usage already includes the tools and prompt. Allow for a larger
            # startup prompt after resume; never sum usage from multiple calls.
            return baseline + trailing + max(0, current_system - stored_system)
        return estimate_request(context)


def select_retained_entries(entries: list[ContextMessageEntry], *, recent_budget: int = KEEP_RECENT_TOKENS) -> list[str]:
    """Keep a recent suffix of whole model rounds and pin current task users."""
    assistant_indices = [
        index for index, item in enumerate(entries) if isinstance(item.message, AssistantMessage)
    ]
    start = len(entries)
    recent_tokens = 0
    for index in reversed(assistant_indices[-KEEP_RECENT_ROUNDS:]):
        end = index + 1
        while end < len(entries) and isinstance(entries[end].message, ToolResultMessage):
            end += 1
        size = sum(estimate_message(item.message) for item in entries[index:end])
        if start != len(entries) and recent_tokens + size > recent_budget:
            break
        start = index
        recent_tokens += size

    task_start = next(
        (index for index in range(len(entries) - 1, -1, -1) if entries[index].task_start),
        None,
    )
    if task_start is None:
        # Old files did not record task boundaries; the latest real user is the fallback.
        task_start = next((
            index for index in range(len(entries) - 1, -1, -1)
            if isinstance(entries[index].message, UserMessage) and not entries[index].is_summary
        ), len(entries))
    return [
        item.id for index, item in enumerate(entries)
        if not item.is_summary and (
            isinstance(item.message, SystemMessage) or index >= start
            or (index >= task_start and isinstance(item.message, UserMessage))
        )
    ]
