"""Run the Agent with pi-style coding tools. This example calls the real model."""

import argparse
import asyncio
from pathlib import Path

import httpx

from mini_pi.agent.agent import Agent
from mini_pi.agent.events import (
    AgentEndEvent,
    ModelEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from mini_pi.ai.events import TextDeltaEvent
from mini_pi.ai.providers.deepseek import DeepSeekProvider
from mini_pi.ai.types import ModelOptions, ModelSpec, TextContent
from mini_pi.coding.tools import create_tools
from mini_pi.config import load_connection_config


async def main(prompt: str, shell: str) -> None:
    config = load_connection_config()
    model = ModelSpec(
        id=config.model_id,
        name=config.model_id,
        provider=config.provider_id,
        api="chat-completions",
        base_url=config.base_url,
    )
    cwd = Path.cwd()
    tools = create_tools(cwd, ("read", shell, "edit", "write"))
    async with httpx.AsyncClient(timeout=120.0) as client:
        provider = DeepSeekProvider(client=client, model=model, api_key=config.api_key)
        agent = Agent(
            provider=provider,
            model=model,
            tools=tools,
            system_prompt=(
                f"你是一个 coding agent，工作目录是 {cwd}。"
                f"可用工具：read、{shell}、edit、write。"
                "先读取文件再修改，以实际工具结果为依据。"
                "用 edit 进行精确修改，用 write 创建文件或完整重写。"
                "edit 的多项替换都针对原文件定位，oldText 必须唯一且互不重叠。"
                f"命令必须使用 {shell} 的语法。使用中文回答。"
            ),
            options=ModelOptions(max_output_tokens=4096),
        )
        async for event in agent.prompt(prompt):
            if isinstance(event, ModelEvent) and isinstance(event.event, TextDeltaEvent):
                print(event.event.delta, end="", flush=True)
            elif isinstance(event, ToolExecutionStartEvent):
                print(f"\n[工具开始] {event.call.name}")
            elif isinstance(event, ToolExecutionEndEvent):
                status = "失败" if event.result.message.is_error else "完成"
                print(f"\n[工具{status}] {event.call.name}")
                for block in event.result.message.content:
                    if isinstance(block, TextContent):
                        print(block.text)
                    else:
                        print(f"[图片附件] {block.mime_type}")
            elif isinstance(event, AgentEndEvent):
                print(f"\n[Agent 结束] {event.reason}")
                if event.error_message:
                    print(event.error_message)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="Task to send to the model.")
    parser.add_argument("--shell", choices=("bash", "powershell"), default="bash")
    args = parser.parse_args()
    asyncio.run(main(args.prompt, args.shell))
