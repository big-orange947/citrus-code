import asyncio
import time

import httpx

from mini_pi.ai.completion import complete
from mini_pi.ai.providers.deepseek import DeepSeekProvider
from mini_pi.ai.registry import ProviderRegistry
from mini_pi.ai.types import (
    ModelContext,
    ModelOptions,
    ModelSpec,
    TextContent,
    UserMessage,
    ToolCall,
    ToolDefinition,
)
from mini_pi.config import load_connection_config


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
                content="请调用 read 工具读取 README.md。",
                timestamp=int(time.time() * 1000),
            )
        ],
        tools=[
            ToolDefinition(
                name="read",
                description="Read a UTF-8 text file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The file path to read.",
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            )
        ],
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        registry = ProviderRegistry()

        registry.register(
            DeepSeekProvider(
                client=client,
                model=model,
                api_key=config.api_key,
            )
        )

        provider = registry.get_provider(config.provider_id)

        selected_model = registry.get_model(
            config.provider_id,
            config.model_id,
        )

        message = await complete(
            provider=provider,
            model=selected_model,
            context=context,
            options=ModelOptions(max_output_tokens=256),
        )

    if message.stop_reason in ("error", "aborted"):
        print("调用失败：", message.error_message)
        return

    for block in message.content:
        if isinstance(block, TextContent):
            print(block.text)

        elif isinstance(block, ToolCall):
            print("调用 ID：", block.id)
            print("工具名称：", block.name)
            print("工具参数：", block.arguments)

if __name__ == "__main__":
    asyncio.run(main())