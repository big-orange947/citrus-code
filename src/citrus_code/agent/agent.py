import time
from collections.abc import AsyncGenerator
from contextlib import aclosing

from ..ai.events import DoneEvent, ErrorEvent, PartialEvent
from ..ai.provider import Provider
from ..ai.types import (
    Message,
    ModelOptions,
    ModelSpec,
    SystemMessage,
    UserMessage,
)
from .cancellation import AbortSignal
from .context import ContextPreparer
from .events import (
    AgentEndEvent,
    AgentEvent,
    AssistantMessageCommittedEvent,
    ContextCompactedEvent,
    ModelEvent,
    ToolExecutionEndEvent,
    TurnStartEvent,
    MessageAcceptedEvent,
)
from .hooks import AgentHooks
from .loop import agent_loop
from .message_queue import (
    PendingMessageQueue,
    QueueMode,
)
from .state import AgentState
from .tool_runner import ToolRunner
from .types import AgentTool


class Agent:
    def __init__(
        self,
        provider: Provider,
        model: ModelSpec,
        tools: list[AgentTool],
        system_prompt: str | None = None,
        options: ModelOptions | None = None,
        max_turns: int | None = None,
        hooks: AgentHooks | None = None,
        steering_mode: QueueMode = "one-at-a-time",
        follow_up_mode: QueueMode = "one-at-a-time",
        prepare_context: ContextPreparer | None = None,
    ) -> None:
        if max_turns is not None and max_turns < 1:
            raise ValueError("max_turns must be at least 1")

        self._provider = provider
        self._model = model
        self._runner = ToolRunner(tools=tools)
        self._options = options
        self._max_turns = max_turns
        self._hooks = hooks
        self._prepare_context = prepare_context

        self._abort_signal: AbortSignal | None = None

        self._steering_queue = PendingMessageQueue(
            mode=steering_mode,
        )

        self._follow_up_queue = PendingMessageQueue(
            mode=follow_up_mode,
        )

        self.state = AgentState()

        if system_prompt is not None:
            self.state.messages.append(
                SystemMessage(
                    content=system_prompt,
                    timestamp=int(time.time() * 1000),
                )
            )

    def abort(self) -> None:
        signal = self._abort_signal

        if signal is not None:
            signal.abort()

    def steer(self, text: str) -> None:
        message = self._create_user_message(text)

        self._steering_queue.enqueue(message)


    def replace_messages(
            self,
            messages: list[Message],
    ) -> None:
        """Replace conversation history while the Agent is idle."""
        if self.state.is_running:
            raise RuntimeError(
                "Cannot replace messages while the agent is running"
            )

        if self.has_queued_messages():
            raise RuntimeError(
                "Cannot replace messages while queued messages exist"
            )

        self.state.messages = [
            message.model_copy(deep=True)
            for message in messages
        ]

        self.state.turn = 0
        self.state.current_response = None
        self.state.error_message = None


    def follow_up(self, text: str) -> None:
        message = self._create_user_message(text)

        self._follow_up_queue.enqueue(message)

    def has_queued_messages(self) -> bool:
        return (
            self._steering_queue.has_items()
            or self._follow_up_queue.has_items()
        )

    def peek_queued_messages(self) -> list[Message]:
        steering = self._steering_queue.peek()

        if steering:
            return steering

        return self._follow_up_queue.peek()

    def clear_all_queues(self) -> None:
        self._steering_queue.clear()
        self._follow_up_queue.clear()

    def _create_user_message(
        self,
        text: str,
    ) -> UserMessage:
        if not text.strip():
            raise ValueError(
                "The message must not be empty"
            )

        return UserMessage(
            content=text,
            timestamp=int(time.time() * 1000),
        )

    async def prompt(
        self,
        text: str,
    ) -> AsyncGenerator[AgentEvent, None]:
        if self.state.is_running:
            raise RuntimeError(
                "The agent is already running. "
                "Use steer() or follow_up() to queue messages."
            )

        message = self._create_user_message(text)

        signal = AbortSignal()
        self._abort_signal = signal

        self.state.is_running = True
        self.state.turn = 0
        self.state.current_response = None
        self.state.error_message = None

        try:
            self.state.messages.append(message)

            stream = agent_loop(
                provider=self._provider,
                model=self._model,
                messages=self.state.messages,
                tool_runner=self._runner,
                options=self._options,
                max_turns=self._max_turns,
                hooks=self._hooks,
                abort_signal=signal,
                get_steering_messages=(
                    self._steering_queue.drain
                ),
                get_follow_up_messages=(
                    self._follow_up_queue.drain
                ),
                prepare_context=self._prepare_context,
            )

            async with aclosing(stream):
                async for event in stream:
                    self._apply_event(event)
                    yield event

        except Exception as exc:
            self.state.error_message = (
                f"{type(exc).__name__}: {exc}"
            )
            raise

        finally:
            self._abort_signal = None
            self.state.is_running = False
            self.state.current_response = None

    def _apply_event(self, event: AgentEvent) -> None:
        if isinstance(event, TurnStartEvent):
            self.state.turn = event.turn

        elif isinstance(event, MessageAcceptedEvent):
            self.state.messages.append(event.message)

        elif isinstance(
            event,
            AssistantMessageCommittedEvent,
        ):
            self.state.messages.append(event.message)
            self.state.current_response = None

        elif isinstance(event, ModelEvent):
            model_event = event.event

            if isinstance(model_event, PartialEvent):
                self.state.current_response = (
                    model_event.partial
                )

            elif isinstance(model_event, DoneEvent):
                self.state.current_response = None

            elif isinstance(model_event, ErrorEvent):
                self.state.current_response = None

        elif isinstance(event, ToolExecutionEndEvent):
            self.state.messages.append(
                event.result.message
            )

        elif isinstance(event, ContextCompactedEvent):
            self.state.messages = event.messages.copy()
            self.state.current_response = None

        elif isinstance(event, AgentEndEvent):
            self.state.messages = event.messages
            self.state.error_message = (
                event.error_message
            )
            self.state.current_response = None
