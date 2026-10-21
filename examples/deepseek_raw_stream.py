import asyncio
import time

import httpx

from citrus_code.ai.events import DoneEvent, ErrorEvent, TextDeltaEvent

from citrus_code.ai.providers.chat_completions import (
    build_chat_request,
    stream_chat_data,
    stream_assistant_events,
)
from citrus_code.ai.types import (
    ModelContext,
    ModelOptions,
    ModelSpec,
    UserMessage,
)
from citrus_code.config import load_connection_config


async def main() -> None:
    config = load_connection_config()

    model = ModelSpec(
        id=config.model_id,
        name=config.model_id,
        provider=config.provider_id,
        api="chat-completions",
        base_url=config.base_url,
    )

    context = ModelContext(
        messages=[
            UserMessage(
                content="用一句话介绍你自己。",
                timestamp=int(time.time() * 1000),
            )
        ]
    )

    body = build_chat_request(
        model=model,
        context=context,
        options=ModelOptions(max_output_tokens=256),
    )

    body["thinking"] = {"type": "disabled"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        data_stream = stream_chat_data(
        client=client,
        model=model,
        api_key=config.api_key,
        body=body,
    )

        async for event in stream_assistant_events(
            model=model,
            data_stream=data_stream,
        ):
            if isinstance(event, TextDeltaEvent):
                print(event.delta, end="", flush=True)

            elif isinstance(event, DoneEvent):
                print()
                print("停止原因：", event.reason)
                print("用量：", event.message.usage)

            elif isinstance(event, ErrorEvent):
                print()
                print("调用失败：", event.error.error_message)


if __name__ == "__main__":
    asyncio.run(main())