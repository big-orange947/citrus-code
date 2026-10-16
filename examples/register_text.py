import asyncio
import time

from mini_pi.ai.completion import complete
from mini_pi.ai.providers.fake import FakeProvider
from mini_pi.ai.registry import ProviderRegistry
from mini_pi.ai.types import (
    ModelContext,
    TextContent,
    UserMessage,
)


async def main() -> None:
    registry = ProviderRegistry()

    registry.register(
        FakeProvider(
            chunks=["通过","注册表","找到模型并完成调用"],
            delay_seconds=0.2,
        )
    )

    provider_id = "fake"
    model_id = "fake-text"

    provider = registry.get_provider(provider_id)
    model = registry.get_model(provider_id,model_id)

    context = ModelContext(
        messages=[
            UserMessage(
                content="请介绍一下自己",
                timestamp=int(time.time()*1000),
            )
        ]
    )


    message = await complete(
        provider=provider,
        model=model,
        context=context,
    )

    for block in message.content:
        if isinstance(block,TextContent):
            print(block.text)

    print("停止原因：",message.stop_reason)

if __name__=="__main__":
    asyncio.run(main())