import asyncio
import time
from pathlib import Path

import httpx

from citrus_code.agent.events import (
    AgentEndEvent,
    AgentStartEvent,
    ModelEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnStartEvent,
)
from citrus_code.agent.loop import agent_loop
from citrus_code.agent.tool_runner import ToolRunner
from citrus_code.ai.events import DoneEvent, TextDeltaEvent
from citrus_code.ai.providers.deepseek import DeepSeekProvider
from citrus_code.ai.types import (
    Message,
    ModelOptions,
    ModelSpec,
    SystemMessage,
    UserMessage,
)
from citrus_code.coding.tools.read import ReadTool
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

    runner = ToolRunner(
        tools=[
            ReadTool(cwd=Path.cwd()),
        ]
    )

    timestamp = int(time.time() * 1000)

    messages: list[Message] = [
        SystemMessage(
            content=(
                "你是一个 coding agent。"
                "需要文件信息时使用工具，以实际读取结果为依据回答。"
            ),
            timestamp=timestamp,
        ),
        UserMessage(
            content=(
                "请读取 pyproject.toml，"
                "告诉我项目名称以及声明的直接依赖。"
            ),
            timestamp=timestamp,
        ),
    ]

    async with httpx.AsyncClient(timeout=120.0) as client:
        provider = DeepSeekProvider(
            client=client,
            model=model,
            api_key=config.api_key,
        )

        async for event in agent_loop(
            provider=provider,
            model=model,
            messages=messages,
            tool_runner=runner,
            options=ModelOptions(max_output_tokens=1024),
        ):
            if isinstance(event, AgentStartEvent):
                print("Agent 开始")

            elif isinstance(event, TurnStartEvent):
                print(f"\n第 {event.turn} 轮")

            elif isinstance(event, ModelEvent):
                model_event = event.event

                if isinstance(model_event, TextDeltaEvent):
                    print(
                        model_event.delta,
                        end="",
                        flush=True,
                    )

                elif isinstance(model_event, DoneEvent):
                    print()
                    print("模型本轮停止原因：", model_event.reason)

            elif isinstance(event, ToolExecutionStartEvent):
                print("开始执行工具：", event.call.name)
                print("工具参数：", event.call.arguments)

            elif isinstance(event, ToolExecutionEndEvent):
                print("工具执行结束")
                print("是否失败：", event.result.message.is_error)
                print("执行详情：", event.result.details)

            elif isinstance(event, AgentEndEvent):
                print("\nAgent 结束")
                print("结束原因：", event.reason)
                print("历史消息数：", len(event.messages))

                if event.error_message is not None:
                    print("错误：", event.error_message)


if __name__ == "__main__":
    asyncio.run(main())