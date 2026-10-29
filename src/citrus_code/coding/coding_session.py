from typing import Optional
import time

from collections.abc import AsyncGenerator
from ..compat import aclosing
from ..agent.agent import Agent
from ..agent.hooks import AgentHooks
from ..agent.types import AgentTool
from ..ai.provider import Provider
from ..ai.types import (
    ModelOptions,
    ModelSpec,
    SystemMessage,
    Message,
    TextContent,
    ToolResultMessage,
)
from .session import SessionManager
from .compaction import CompactionController

from .history import get_pending_tool_calls

from ..agent.events import (
    AgentEvent,
    AgentStartEvent,
    AssistantMessageCommittedEvent,
    ContextCompactedEvent,
    MessageAcceptedEvent,
    ToolExecutionEndEvent,
)

class CodingSession:
    def __init__(
        self,
        provider: Provider,
        model: ModelSpec,
        tools: list[AgentTool],
        session_manager: SessionManager,
        system_prompt: Optional[str] = None,
        options: Optional[ModelOptions] = None,
        max_turns: Optional[int] = None,
        hooks: Optional[AgentHooks] = None,
        auto_compact: bool = True,
    ) -> None:
        if provider.id != model.provider:
            raise ValueError(
                "The provider does not match the model"
            )

        context = session_manager.build_context()

        if context.provider is not None:
            if (
                context.provider != model.provider
                or context.model_id != model.id
            ):
                raise ValueError(
                    "The selected model does not match "
                    "the model recorded in the session"
                )
        self._provider_id = model.provider
        self._model_id = model.id
        self._model = model.model_copy(deep=True)
        self._tools = list(tools)
        self._options = options
        self._max_turns = max_turns
        self._hooks = hooks
        self.compaction = (
            CompactionController(provider, model, session_manager)
            if auto_compact else None
        )
        self.agent = Agent(
            provider=provider,
            model=model,
            tools=tools,
            options=options,
            max_turns=max_turns,
            hooks=hooks,
            prepare_context=self.compaction.prepare if self.compaction is not None else None,
        )

        self.session_manager = session_manager

        if context.provider is None:
            self.session_manager.append_model_change(
                provider=model.provider,
                model_id=model.id,
            )

        if not context.messages and system_prompt is not None:
            self.session_manager.append_message(
                SystemMessage(
                    content=system_prompt,
                    timestamp=int(time.time() * 1000),
                )
            )

        restored_context = (
            self.session_manager.build_context()
        )

        self.agent.replace_messages(
            restored_context.messages
        )

    @property
    def model(self) -> ModelSpec:
        return self._model.model_copy(deep=True)

    def replace_tools(self, tools: list[AgentTool]) -> None:
        if self.is_running or self.has_queued_messages():
            raise ValueError("更新工具需要当前任务结束且没有排队消息。")
        self.agent.replace_tools(tools)
        self._tools = list(tools)

    def switch_model(
        self, provider: Provider, model: ModelSpec,
        *, options: Optional[ModelOptions] = None, hooks: Optional[AgentHooks] = None,
    ) -> None:
        """Prepare a new runtime, commit the selection, then publish it in memory."""
        if self.is_running or self.has_queued_messages():
            raise ValueError("切换模型需要当前任务结束且没有排队消息；可先 /abort 或 /clear-queue。")
        if provider.id != model.provider:
            raise ValueError("The provider does not match the model")
        context = self.session_manager.build_context()
        if context.messages != self.agent.state.messages or get_pending_tool_calls(context.messages):
            raise ValueError("切换前需要恢复一致且完整的会话上下文。")
        selected_options = options if options is not None else self._options
        selected_hooks = hooks if hooks is not None else self._hooks
        compaction = CompactionController(provider, model, self.session_manager) if self.compaction is not None else None
        agent = Agent(
            provider=provider, model=model, tools=self._tools,
            options=selected_options, max_turns=self._max_turns, hooks=selected_hooks,
            prepare_context=compaction.prepare if compaction is not None else None,
        )
        agent.replace_messages(context.messages)
        # A failed append must leave the running provider and model unchanged.
        self.session_manager.append_model_change(model.provider, model.id)
        self.agent, self.compaction = agent, compaction
        self._provider_id, self._model_id = model.provider, model.id
        self._model = model.model_copy(deep=True)
        self._options, self._hooks = selected_options, selected_hooks


    async def prompt(
            self,
            text: str,
    ) -> AsyncGenerator[AgentEvent, None]:
        if self.agent.state.is_running:
            raise RuntimeError(
                "The coding session is already running"
            )

        context = self.session_manager.build_context()

        if get_pending_tool_calls(context.messages):
            raise RuntimeError(
                "The session has unfinished tool calls. "
                "Recover the session before continuing."
            )

        if context.messages != self.agent.state.messages:
            raise RuntimeError(
                "Agent history does not match the session. "
                "Restore the session before continuing."
            )

        expected_leaf_id = self.session_manager.leaf_id

        stream = self.agent.prompt(text)

        async with aclosing(stream):
            async for event in stream:
                if(
                    self.session_manager.leaf_id
                    !=expected_leaf_id
                ):
                    raise RuntimeError(
                        "The session branch changed "
                        "while the agent was running"
                    )

                message: Optional[Message] = None
                task_start = False

                if isinstance(event, AgentStartEvent):
                    message = self.agent.state.messages[-1]
                    task_start = True

                elif isinstance(
                    event,
                    (
                        MessageAcceptedEvent,
                        AssistantMessageCommittedEvent,
                    ),
                ):
                    message = event.message
                    task_start = isinstance(event, MessageAcceptedEvent) and event.source == "follow_up"

                elif isinstance(
                    event,
                    ToolExecutionEndEvent,
                ):
                    message = event.result.message

                elif isinstance(event, ContextCompactedEvent):
                    if self.compaction is None:
                        raise RuntimeError("Received compaction without a controller")
                    expected_leaf_id = self.compaction.commit(event)

                if message is not None:
                    expected_leaf_id = (
                        self.session_manager.append_message(
                            message, task_start=task_start,
                        )
                    )

                yield event



    @property
    def is_running(self) -> bool:
        return self.agent.state.is_running

    def abort(self) -> None:
        self.agent.abort()

    def steer(self, text: str) -> None:
        self.agent.steer(text)

    def follow_up(self, text: str) -> None:
        self.agent.follow_up(text)

    def has_queued_messages(self) -> bool:
        return self.agent.has_queued_messages()

    def checkout(self, entry_id: str) -> None:
        if self.is_running:
            raise RuntimeError(
                "Cannot switch branches while running"
            )

        if self.has_queued_messages():
            raise RuntimeError(
                "Cannot switch branches while queued messages exist"
            )

        context = (
            self.session_manager.build_context_at(entry_id)
        )

        if (
            context.provider != self._provider_id
            or context.model_id != self._model_id
        ):
            raise ValueError(
                "The target branch uses a different model"
            )

        if not context.messages:
            raise ValueError(
                "Select a branch containing messages"
            )

        if get_pending_tool_calls(context.messages):
            raise ValueError(
                "The target branch has unfinished tool calls"
            )

        self.session_manager.checkout(entry_id)

        self.agent.replace_messages(
            context.messages
        )

    def recover_pending_tools(self) -> int:
        if self.is_running:
            raise RuntimeError(
                "Cannot recover while running"
            )
        if self.has_queued_messages():
            raise RuntimeError("Cannot recover while queued messages exist")

        context = self.session_manager.build_context()

        if(
            context.provider != self._provider_id
            or context.model_id !=self._model_id
        ):
            raise ValueError(
                "The session model does not match "
                "the current agent"
            )

        pending_calls = get_pending_tool_calls(
            context.messages
        )

        for call in pending_calls:
            message = ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=[
                    TextContent(
                        text=(
                            "The session was interrupted before "
                            "a result was recorded for this tool call. "
                            "Its execution status is unknown: "
                            "it may not have started, may have "
                            "partially run, or may have completed. "
                            "Inspect the actual state before "
                            "deciding whether to retry."
                        )
                    ),
                ],
                is_error=True,
                timestamp=int(time.time() * 1000),
            )

            self.session_manager.append_message(message)

        restored_context = (
            self.session_manager.build_context()
        )

        self.agent.replace_messages(
            restored_context.messages
        )

        return len(pending_calls)
