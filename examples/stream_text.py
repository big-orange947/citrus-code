import asyncio
import time

from citrus_code.ai.events import DoneEvent, TextDeltaEvent
from citrus_code.ai.provider import Provider
from citrus_code.ai.providers.fake import FakeProvider
from citrus_code.ai.types import ModelContext, UserMessage


async def main() -> None:
    provider: Provider = FakeProvider(
        chunks=[
            "这是",
            "模拟模型",
            "逐段生成的回答。",
        ],
        delay_seconds=0.3,
    )

    model = provider.get_models()[0]

    context = ModelContext(
        messages=[
            UserMessage(
                content="请介绍一下自己。",
                timestamp=int(time.time() * 1000),
            )
        ]
    )

    async for event in provider.stream(model, context):
        if isinstance(event, TextDeltaEvent):
            print(event.delta, end="", flush=True)

        elif isinstance(event, DoneEvent):
            print()
            print("停止原因：", event.reason)


if __name__ == "__main__":
    asyncio.run(main())