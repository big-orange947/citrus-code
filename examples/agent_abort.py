import asyncio

from mini_pi.agent.agent import Agent
from mini_pi.agent.events import (
    AgentEndEvent,
    ModelEvent,
)
from mini_pi.ai.events import TextDeltaEvent
from mini_pi.ai.providers.fake import FakeProvider


async def main() -> None:
    provider = FakeProvider(
        chunks=[
            "第一段。",
            "第二段。",
            "第三段。",
            "第四段。",
        ],
        delay_seconds=0.3,
    )

    model = provider.get_models()[0]

    agent = Agent(
        provider=provider,
        model=model,
        tools=[],
    )

    print("第一次运行：收到两段文字后取消")

    chunk_count = 0

    async for event in agent.prompt("第一次提问"):
        if isinstance(event, ModelEvent):
            model_event = event.event

            if isinstance(model_event, TextDeltaEvent):
                print(
                    model_event.delta,
                    end="",
                    flush=True,
                )

                chunk_count += 1

                if chunk_count == 2:
                    print("\n请求取消")
                    agent.abort()

                    print(
                        "刚请求取消，是否仍在运行：",
                        agent.state.is_running,
                    )

        elif isinstance(event, AgentEndEvent):
            print("结束原因：", event.reason)

            if event.error_message is not None:
                print("错误：", event.error_message)

    print(
        "第一次运行结束后：",
        agent.state.is_running,
    )

    print("\n第二次运行：让它正常完成")

    async for event in agent.prompt("第二次提问"):
        if isinstance(event, ModelEvent):
            model_event = event.event

            if isinstance(model_event, TextDeltaEvent):
                print(
                    model_event.delta,
                    end="",
                    flush=True,
                )

        elif isinstance(event, AgentEndEvent):
            print("\n结束原因：", event.reason)

            if event.error_message is not None:
                print("错误：", event.error_message)

    print(
        "第二次运行结束后：",
        agent.state.is_running,
    )

    print("\n最终历史中的消息角色：")

    for message in agent.state.messages:
        print("-", message.role)


if __name__ == "__main__":
    asyncio.run(main())