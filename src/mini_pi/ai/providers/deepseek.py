from collections.abc import AsyncIterator

import httpx
from pydantic import SecretStr

from ..events import AssistantMessageEvent
from ..types import (
    ImageContent, Message, ModelContext, ModelOptions, ModelSpec,
    TextContent, ToolResultMessage, UserMessage,
)
from .chat_completions import (
    build_chat_request,
    stream_chat_data,
    stream_assistant_events,
)


DEEPSEEK_VISION_MODEL_IDS = frozenset({
    "deepseek-flash",
    "deepseek-v4-flash",
    "deepseek-v4-flash-vision-exp",
})
DEEPSEEK_IMAGE_MIME_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})


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

        if model.supports_images:
            messages: list[Message] = []
            for message in context.messages:
                if isinstance(message, (UserMessage, ToolResultMessage)) and not isinstance(message.content, str):
                    content = [
                        TextContent(text=(
                            f"[图片未发送：DeepSeek 不支持 {block.mime_type}；"
                            "请先转换为 PNG、JPEG、GIF 或 WebP。]"
                        ))
                        if isinstance(block, ImageContent) and block.mime_type not in DEEPSEEK_IMAGE_MIME_TYPES
                        else block
                        for block in message.content
                    ]
                    message = message.model_copy(update={"content": content})
                messages.append(message)
            # Transform only the request. An unsupported old attachment must
            # not make every later request in the session fail with HTTP 400.
            context = ModelContext(messages=messages, tools=context.tools)

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
