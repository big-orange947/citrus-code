import asyncio
from pathlib import Path

import httpx

from mini_pi.agent.agent import Agent
from mini_pi.agent.events import (
    AgentEndEvent,
    ModelEvent,
    ToolExecutionStartEvent,
)
from mini_pi.ai.events import TextDeltaEvent
from mini_pi.ai.providers.deepseek import DeepSeekProvider
from mini_pi.ai.types import ModelOptions, ModelSpec
from mini_pi.coding.tools.read import ReadTool
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

    async with httpx.AsyncClient(timeout=120.0) as client:
        provider = DeepSeekProvider(
            client=client,
            model=model,
            api_key=config.api_key,
        )

        agent = Agent(
            provider=provider,
            model=model,
            tools=[ReadTool(cwd=Path.cwd())],
            system_prompt=(
                "你是一个 coding agent。"
                "需要文件信息时使用工具，以实际结果为依据回答。"
            ),
            options=ModelOptions(max_output_tokens=1024),
        )

        questions = [
            "读取 pyproject.toml，告诉我项目名称和直接依赖。",
            "刚才读取的配置里，项目要求的 Python 版本是什么？",
        ]

        for question in questions:
            print("\n用户：", question)

            async for event in agent.prompt(question):
                if isinstance(event, ModelEvent):
                    if isinstance(event.event, TextDeltaEvent):
                        print(
                            event.event.delta,
                            end="",
                            flush=True,
                        )

                elif isinstance(event, ToolExecutionStartEvent):
                    print("\n执行工具：", event.call.name)

                elif isinstance(event, AgentEndEvent):
                    print("\n结束原因：", event.reason)

                    if event.error_message is not None:
                        print("错误：", event.error_message)

            print("当前是否运行：", agent.state.is_running)
            print("历史消息数：", len(agent.state.messages))


if __name__ == "__main__":
    asyncio.run(main())