from typing import Optional, Union
import time
from collections.abc import (
    AsyncGenerator,
    Callable,
    Iterator,
)
from ..compat import aclosing
from typing import Literal

from ..ai.events import DoneEvent, ErrorEvent
from ..ai.provider import Provider
from ..ai.types import (
    Message,
    ModelContext,
    ModelOptions,
    ModelSpec,
    TextContent,
    ToolCall,
    ToolResultMessage,
)
from .cancellation import (
    AgentAbortedError,
    AbortSignal,
    wait_with_abort,
)
from .events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    AssistantMessageCommittedEvent,
    ContextCompactedEvent,
    MessageAcceptedEvent,
    ModelEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnStartEvent,
)
from .hooks import AgentHooks
from .context import ContextPreparer
from .tool_runner import ToolRunner
from .types import ToolRunResult


MessageReader = Callable[[], list[Message]]


def _accept_messages(
    history: list[Message],
    messages: list[Message],
    source: Literal["steering", "follow_up"],
) -> Iterator[AgentEvent]:
    # 先将整批消息写入历史，再对外发送事件。
    history.extend(messages)

    for message in messages:
        yield MessageAcceptedEvent(
            message=message,
            source=source,
        )


def _finish_pending_tools(
    history: list[Message],
    pending_calls: list[ToolCall],
    started_call_id: Optional[str],
    reason: Literal["aborted", "error"],
) -> Iterator[AgentEvent]:
    """Give every unfinished call a corresponding result."""
    for call in pending_calls:
        started = call.id == started_call_id

        if not started:
            yield ToolExecutionStartEvent(call=call)

        if started:
            text = (
                f"Tool call was interrupted because the Agent "
                f"ended with reason '{reason}'. "
                "Side effects may have occurred. "
                "Inspect the actual state before retrying."
            )
        else:
            text = (
                f"Tool call was not executed because the Agent "
                f"ended with reason '{reason}'."
            )

        result = ToolRunResult(
            message=ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=[
                    TextContent(text=text),
                ],
                is_error=True,
                timestamp=int(time.time() * 1000),
            ),
            details={
                "reason": reason,
                "status": (
                    "interrupted"
                    if started
                    else "not_started"
                ),
            },
        )

        history.append(result.message)

        yield ToolExecutionEndEvent(
            call=call,
            result=result,
        )

    pending_calls.clear()


async def agent_loop(
    provider: Provider,
    model: ModelSpec,
    messages: list[Message],
    tool_runner: ToolRunner,
    options: Optional[ModelOptions] = None,
    max_turns: Optional[int] = None,
    hooks: Optional[AgentHooks] = None,
    abort_signal: Optional[AbortSignal] = None,
    get_steering_messages: Optional[MessageReader] = None,
    get_follow_up_messages: Optional[MessageReader] = None,
    prepare_context: Optional[ContextPreparer] = None,
) -> AsyncGenerator[AgentEvent, None]:
    if max_turns is not None and max_turns < 1:
        raise ValueError("max_turns must be at least 1")

    if not messages:
        raise ValueError(
            "messages must contain at least one message"
        )

    signal = (
        abort_signal
        if abort_signal is not None
        else AbortSignal()
    )

    history = messages.copy()
    turn = 0

    pending_calls: list[ToolCall] = []
    started_call_id: Optional[str] = None

    # 上一轮结束时，可能已经为下一轮接纳了队列消息。
    skip_steering_poll = False

    yield AgentStartEvent()

    try:
        while True:
            signal.raise_if_aborted()

            if (
                max_turns is not None
                and turn >= max_turns
            ):
                yield AgentEndEvent(
                    reason="max_turns",
                    messages=history,
                )
                return

            if (
                not skip_steering_poll
                and get_steering_messages is not None
            ):
                queued = get_steering_messages()

                for event in _accept_messages(
                    history=history,
                    messages=queued,
                    source="steering",
                ):
                    yield event

            skip_steering_poll = False

            signal.raise_if_aborted()

            turn += 1

            yield TurnStartEvent(turn=turn)

            signal.raise_if_aborted()

            context = ModelContext(
                messages=history,
                tools=tool_runner.get_definitions(),
            )

            if hooks is not None:
                context = await wait_with_abort(
                    operation=hooks.before_model(
                        context.model_copy(deep=True)
                    ),
                    signal=signal,
                )

                if not isinstance(context, ModelContext):
                    raise TypeError(
                        "before_model must return a ModelContext"
                    )

            if prepare_context is not None:
                prepared = await wait_with_abort(
                    prepare_context(context.model_copy(deep=True), signal), signal
                )
                if prepared is not None:
                    history = prepared.messages.copy()
                    context = prepared.context
                    # The consumer persists this change before requesting another event.
                    yield ContextCompactedEvent(
                        messages=history.copy(),
                        tokens_before=prepared.tokens_before,
                        tokens_after=prepared.tokens_after,
                    )

            signal.raise_if_aborted()

            terminal: Optional[Union[DoneEvent, ErrorEvent]] = None

            stream = provider.stream(
                model=model,
                context=context,
                options=options,
            )

            if not isinstance(stream, AsyncGenerator):
                raise TypeError(
                    "Provider.stream must return an "
                    "async generator that can be closed"
                )

            async with aclosing(stream):
                while True:
                    try:
                        event = await wait_with_abort(
                            operation=stream.__anext__(),
                            signal=signal,
                        )
                    except StopAsyncIteration:
                        break

                    if terminal is not None:
                        raise RuntimeError(
                            "Received a model event "
                            "after a terminal event"
                        )

                    if isinstance(
                        event,
                        (DoneEvent, ErrorEvent),
                    ):
                        terminal = event

                    yield ModelEvent(event=event)

            if terminal is None:
                raise RuntimeError(
                    "The model stream ended "
                    "without a terminal event"
                )

            if isinstance(terminal, ErrorEvent):
                assistant = terminal.error
            else:
                assistant = terminal.message

            if hooks is not None:
                await wait_with_abort(
                    operation=hooks.after_model(
                        assistant.model_copy(deep=True)
                    ),
                    signal=signal,
                )

            if isinstance(terminal, ErrorEvent):
                yield AgentEndEvent(
                    reason=terminal.reason,
                    messages=history,
                    error_message=assistant.error_message,
                )
                return

            reason = terminal.reason

            if assistant.stop_reason != reason:
                raise RuntimeError(
                    "The final message stop reason "
                    "does not match the terminal event"
                )

            calls: list[ToolCall] = []

            for block in assistant.content:
                if isinstance(block, ToolCall):
                    calls.append(block)

            if reason == "toolUse" and not calls:
                raise RuntimeError(
                    "The model requested tool use "
                    "without tool calls"
                )

            if reason != "toolUse" and calls:
                raise RuntimeError(
                    "Tool calls require "
                    "the toolUse stop reason"
                )

            history.append(assistant)

            pending_calls = calls.copy()

            yield AssistantMessageCommittedEvent(
                message=assistant,
            )

            if reason == "toolUse":
                while pending_calls:
                    signal.raise_if_aborted()

                    call = pending_calls[0]
                    started_call_id = call.id

                    yield ToolExecutionStartEvent(
                        call=call,
                    )

                    result = await wait_with_abort(
                        operation=tool_runner.execute(
                            call=call,
                            hooks=hooks,
                        ),
                        signal=signal,
                    )

                    history.append(result.message)

                    pending_calls.pop(0)
                    started_call_id = None

                    yield ToolExecutionEndEvent(
                        call=call,
                        result=result,
                    )

                continue

            # 只有正常 stop，才检查是否有排队消息需要续接。
            # 轮数预算耗尽时保留队列，不取出无法处理的消息。
            can_continue = (
                max_turns is None
                or turn < max_turns
            )

            if reason == "stop" and can_continue:
                signal.raise_if_aborted()

                queued: list[Message] = []
                source: Literal[
                    "steering", "follow_up"
                ] = "steering"

                if get_steering_messages is not None:
                    queued = get_steering_messages()

                if (
                    not queued
                    and get_follow_up_messages is not None
                ):
                    queued = get_follow_up_messages()
                    source = "follow_up"

                if queued:
                    for event in _accept_messages(
                        history=history,
                        messages=queued,
                        source=source,
                    ):
                        yield event

                    skip_steering_poll = True
                    continue

            yield AgentEndEvent(
                reason=reason,
                messages=history,
                error_message=assistant.error_message,
            )
            return

    except AgentAbortedError:
        for event in _finish_pending_tools(
            history=history,
            pending_calls=pending_calls,
            started_call_id=started_call_id,
            reason="aborted",
        ):
            yield event

        yield AgentEndEvent(
            reason="aborted",
            messages=history,
        )

    except Exception as exc:
        for event in _finish_pending_tools(
            history=history,
            pending_calls=pending_calls,
            started_call_id=started_call_id,
            reason="error",
        ):
            yield event

        yield AgentEndEvent(
            reason="error",
            messages=history,
            error_message=(
                f"{type(exc).__name__}: {exc}"
            ),
        )
