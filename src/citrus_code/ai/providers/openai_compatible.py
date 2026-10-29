"""OpenAI Chat Completions providers, including configurable relay endpoints."""

from collections.abc import AsyncGenerator, Callable
from typing import Optional

import httpx
from pydantic import JsonValue, SecretStr

from ..events import AssistantMessageEvent
from ..retry import stream_with_retries
from ..types import ModelContext, ModelOptions, ModelSpec
from .chat_completions import build_chat_request, stream_assistant_events, stream_chat_data
from .transport import apply_extra_body


class OpenAICompatibleProvider:
    def __init__(
        self, client: httpx.AsyncClient, models: list[ModelSpec], api_key: SecretStr,
        *, provider_id: str, name: Optional[str] = None, auth_type: str = "bearer",
        headers: Optional[dict[str, str]] = None, extra_body: Optional[dict[str, JsonValue]] = None,
        include_usage: bool = True, max_tokens_field: str = "max_tokens",
        reasoning_effort: Optional[Callable[[], str]] = None,
    ) -> None:
        if not models or any(model.provider != provider_id or model.api != "chat-completions" for model in models):
            raise ValueError("Models must belong to this Chat Completions provider")
        if max_tokens_field not in ("max_tokens", "max_completion_tokens"):
            raise ValueError("Unsupported output token parameter")
        self.id = provider_id
        self.name = name or provider_id
        self._client = client
        self._models = {model.id: model.model_copy(deep=True) for model in models}
        self._api_key = api_key
        self._auth_type = auth_type
        self._headers = dict(headers or {})
        self._extra_body = dict(extra_body or {})
        self._include_usage = include_usage
        self._max_tokens_field = max_tokens_field
        self._reasoning_effort = reasoning_effort

    def get_models(self) -> list[ModelSpec]:
        return [model.model_copy(deep=True) for model in self._models.values()]

    def stream(
        self, model: ModelSpec, context: ModelContext, options: Optional[ModelOptions] = None,
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        if self._models.get(model.id) != model:
            raise ValueError("The requested model is not registered with this provider")
        body = build_chat_request(model, context, options)
        if not self._include_usage:
            body.pop("stream_options", None)
        if self._max_tokens_field != "max_tokens" and "max_tokens" in body:
            body[self._max_tokens_field] = body.pop("max_tokens")
        apply_extra_body(body, self._extra_body)
        if self._reasoning_effort is not None:
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = self._reasoning_effort()
            if context.tools:
                for message in body["messages"]:
                    if isinstance(message, dict) and message.get("role") == "assistant":
                        message.setdefault("reasoning_content", "")
        return stream_with_retries(lambda: stream_assistant_events(
            model=model, data_stream=stream_chat_data(
                self._client, model, self._api_key, body,
                headers=self._headers, auth_type=self._auth_type,
            ),
        ))
