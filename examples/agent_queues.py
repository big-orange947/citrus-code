import asyncio

from citrus_code.agent.agent import Agent
from citrus_code.agent.events import (
    AgentEndEvent,
    MessageAcceptedEvent,
    ModelEvent,
    TurnStartEvent,
)
from citrus_code.agent.hooks import AgentHooks
from citrus_code.ai.events import TextDeltaEvent
from citrus_code.ai.providers.fake import FakeProvider
from citrus_code.ai.types import ModelContext, UserMessage


class InspectContextHooks(AgentHooks):
    async def before_model(
        self,
        context: ModelContext,
    ) -> ModelContext:
        print(
            "本次请求的消息数：",
            len(context.messages),
        )

        last_message = context.messages[-1]

        if isinstance(last_message, UserMessage):
            print(
                "本次请求最后的用户消息：",
                last_message.content,
            )

        return context


async def main() -> None:
    provider = FakeProvider(
        chunks=["这一轮处理完成。"],
        delay_seconds=0.2,
    )

    model = provider.get_models()[0]

    agent = Agent(
        provider=provider,
        model=model,
        tools=[],
        hooks=InspectContextHooks(),
    )

    queued = False

    async for event in agent.prompt("实现登录功能"):
        if isinstance(event, TurnStartEvent):
            print(f"\n[第 {event.turn} 轮开始]")

        elif isinstance(event, ModelEvent):
            if isinstance(event.event, TextDeltaEvent):
                print(event.event.delta)

                if not queued:
                    queued = True

                    # 故意先排 follow-up，再排 steering。
                    agent.follow_up(
                        "完成后再补使用说明"
                    )

                    agent.steer(
                        "登录方式改成邮箱验证码"
                    )

                    print(
                        "已入队：先 follow-up，"
                        "再 steering"
                    )

        elif isinstance(event, MessageAcceptedEvent):
            print(
                "\n[消息已接纳]",
                event.source,
            )

            if isinstance(event.message, UserMessage):
                print(event.message.content)

            print(
                "当前状态中的消息数：",
                len(agent.state.messages),
            )

        elif isinstance(event, AgentEndEvent):
            print(
                "\n[Agent 结束]",
                event.reason,
            )

            if event.error_message is not None:
                print(
                    "错误：",
                    event.error_message,
                )

    print("\n最终轮数：", agent.state.turn)

    print(
        "是否仍在运行：",
        agent.state.is_running,
    )

    print(
        "是否还有排队消息：",
        agent.has_queued_messages(),
    )

    print(
        "最终历史消息数：",
        len(agent.state.messages),
    )

    print("\n最终历史顺序：")

    for message in agent.state.messages:
        print("-", message.role)

        if isinstance(message, UserMessage):
            print(" ", message.content)


if __name__ == "__main__":
    asyncio.run(main())