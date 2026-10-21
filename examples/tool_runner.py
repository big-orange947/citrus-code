import asyncio
from pathlib import Path

from citrus_code.agent.tool_runner import ToolRunner
from citrus_code.ai.types import ToolCall
from citrus_code.coding.tools.read import ReadTool


async def main() -> None:
    runner = ToolRunner(
        tools=[
            ReadTool(cwd=Path.cwd()),
        ]
    )

    calls = [
        ToolCall(
            id="demo_success",
            name="read",
            arguments={
                "path": "pyproject.toml",
                "limit": 5,
            },
        ),
        ToolCall(
            id="demo_invalid_arguments",
            name="read",
            arguments={
                "path": "pyproject.toml",
                "offset": 0,
            },
        ),
        ToolCall(
            id="demo_unknown_tool",
            name="not_registered",
            arguments={},
        ),
    ]

    for call in calls:
        result = await runner.execute(call)

        print("调用 ID：", result.message.tool_call_id)
        print("是否失败：", result.message.is_error)

        for block in result.message.content:
            print(block.text)

        print("内部详情：", result.details)
        print()


if __name__ == "__main__":
    asyncio.run(main())