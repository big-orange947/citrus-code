from collections.abc import AsyncIterator

import httpx
from pydantic import SecretStr

from ..events import AssistantMessageEvent
from ..types import ModelContext, ModelOptions, ModelSpec
from .chat_completions import (
    build_chat_request,
    stream_chat_data,
    stream_assistant_events,
)


class DeepSeekProvider:
    id: str = "deepseek"
    name: str = "DeepSeek"

    def __init__(
        self,
        client: httpx.AsyncClient,
        model: ModelSpec,
        api_key: SecretStr,
    ) -> None:
        if model.provider != self.id:
            raise ValueError(
                "The model does not belong to this provider"
            )

        if model.api != "chat-completions":
            raise ValueError(
                "This provider requires the chat-completions API"
            )

        self._client = client
        self._model = model
        self._api_key = api_key

    def get_models(self) -> list[ModelSpec]:
        return [self._model]

    def stream(
        self,
        model: ModelSpec,
        context: ModelContext,
        options: ModelOptions | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        if model != self._model:
            raise ValueError(
                "The requested model is not registered "
                "with this provider"
            )

        body = build_chat_request(
            model=model,
            context=context,
            options=options,
        )

        body["thinking"] = {"type": "disabled"}

        data_stream = stream_chat_data(
            client=self._client,
            model=model,
            api_key=self._api_key,
            body=body,
        )

        return stream_assistant_events(
            model=model,
            data_stream=data_stream,
        )