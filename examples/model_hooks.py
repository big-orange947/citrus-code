import asyncio
import time
from pathlib import Path

import httpx

from mini_pi.agent.agent import Agent
from mini_pi.agent.events import (
    AgentEndEvent,
    ModelEvent,
    ToolExecutionStartEvent,
)
from mini_pi.agent.hooks import AgentHooks
from mini_pi.ai.events import TextDeltaEvent
from mini_pi.ai.providers.deepseek import DeepSeekProvider
from mini_pi.ai.types import (
    AssistantMessage,
    ModelContext,
    ModelOptions,
    ModelSpec,
    SystemMessage,
)
from mini_pi.coding.tools.read import ReadTool
from mini_pi.config import load_connection_config


class DemoHooks(AgentHooks):
    async def before_model(
        self,
        context: ModelContext,
    ) -> ModelContext:
        print(
            "\n[before_model] 原始请求消息数：",
            len(context.messages),
        )

        context.messages.insert(
            0,
            SystemMessage(
                content=(
                    "本次回答请使用中文。"
                    "给出最终答案时，先给结论，"
                    "再简要说明依据。"
                ),
                timestamp=int(time.time() * 1000),
            ),
        )

        print(
            "[before_model] 添加临时说明后：",
            len(context.messages),
        )

        return context

    async def after_model(
        self,
        message: AssistantMessage,
    ) -> None:
        print(
            "\n[after_model] 停止原因：",
            message.stop_reason,
        )

        if message.usage is not None:
            print(
                "[after_model] 本轮 token 总数：",
                message.usage.total_tokens,
            )

        if message.error_message is not None:
            print(
                "[after_model] 模型错误：",
                message.error_message,
            )


async def main() -> None:
    config = load_connection_config()

    model = ModelSpec(
        id=config.model_id,
        name=config.model_id,
        provider=config.provider_id,
        api="chat-completions",
        base_url=config.base_url,
    )

    async with httpx.AsyncClient(
        timeout=120.0
    ) as client:
        provider = DeepSeekProvider(
            client=client,
            model=model,
            api_key=config.api_key,
        )

        agent = Agent(
            provider=provider,
            model=model,
            tools=[
                ReadTool(cwd=Path.cwd()),
            ],
            system_prompt=(
                "你是一个 coding agent。"
                "需要文件信息时使用工具，"
                "以实际结果为依据回答。"
            ),
            options=ModelOptions(
                max_output_tokens=1024
            ),
            hooks=DemoHooks(),
        )

        async for event in agent.prompt(
            "请使用 read 工具读取 pyproject.toml，"
            "告诉我项目名称和直接依赖。"
        ):
            if isinstance(event, ModelEvent):
                if isinstance(
                    event.event,
                    TextDeltaEvent,
                ):
                    print(
                        event.event.delta,
                        end="",
                        flush=True,
                    )

            elif isinstance(
                event,
                ToolExecutionStartEvent,
            ):
                print(
                    "\n执行工具：",
                    event.call.name,
                )

            elif isinstance(event, AgentEndEvent):
                print(
                    "\nAgent 结束原因：",
                    event.reason,
                )

                if event.error_message is not None:
                    print(
                        "错误：",
                        event.error_message,
                    )

        print("\n保存在历史中的系统消息：")

        for message in agent.state.messages:
            if isinstance(message, SystemMessage):
                print(message.content)


if __name__ == "__main__":
    asyncio.run(main())