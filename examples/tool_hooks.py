import asyncio
from pathlib import Path

from mini_pi.agent.hooks import AgentHooks
from mini_pi.agent.tool_runner import ToolRunner
from mini_pi.agent.types import ToolRunResult
from mini_pi.ai.types import TextContent, ToolCall
from mini_pi.coding.tools.read import ReadTool


class DemoToolHooks(AgentHooks):
    async def before_tool(
        self,
        call: ToolCall,
    ) -> str | None:
        print(
            "[before_tool] 收到调用：",
            call.name,
            call.arguments,
        )

        if call.name == "read":
            path = call.arguments.get("path")

            if isinstance(path, str):
                if Path(path).name == ".env":
                    print("[before_tool] 拒绝执行")

                    return "示例规则：不允许读取 .env 文件"

        print("[before_tool] 允许执行")

        return None

    async def after_tool(
        self,
        call: ToolCall,
        result: ToolRunResult,
    ) -> ToolRunResult:
        print(
            "[after_tool] 收到结果：",
            call.name,
            "is_error =",
            result.message.is_error,
        )

        # 给内部执行报告补充信息。
        result.details["processed_by_hook"] = True

        # 给成功读取的内容添加说明。
        if (
            call.name == "read"
            and not result.message.is_error
        ):
            result.message.content.insert(
                0,
                TextContent(
                    text=(
                        "以下内容来自 read 工具"
                        "实际读取的文件：\n"
                    )
                ),
            )

        return result


async def main() -> None:
    runner = ToolRunner(
        tools=[
            ReadTool(cwd=Path.cwd()),
        ]
    )

    hooks = DemoToolHooks()

    calls = [
        ToolCall(
            id="call_allowed",
            name="read",
            arguments={
                "path": "pyproject.toml",
                "limit": 5,
            },
        ),
        ToolCall(
            id="call_blocked",
            name="read",
            arguments={
                "path": ".env",
            },
        ),
    ]

    for call in calls:
        print("\n开始处理：", call.id)

        result = await runner.execute(
            call=call,
            hooks=hooks,
        )

        print("结果对应的调用 ID：", result.message.tool_call_id)
        print("是否失败：", result.message.is_error)

        print("准备交给模型的内容：")

        for block in result.message.content:
            print(block.text)

        print("内部信息：", result.details)


if __name__ == "__main__":
    asyncio.run(main())