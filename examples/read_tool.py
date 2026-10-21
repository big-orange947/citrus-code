import asyncio
from pathlib import Path

from citrus_code.agent.types import AgentTool
from citrus_code.coding.tools.read import ReadTool


async def main() -> None:
    tool: AgentTool = ReadTool(cwd=Path.cwd())

    definition = tool.get_definition()
    print("工具名称：", definition.name)

    result = await tool.execute(
        tool_call_id="demo_read_001",
        arguments={
            "path": "pyproject.toml",
            "limit": 30,
        },
    )

    for block in result.content:
        print(block.text)

    print("执行详情：", result.details)


if __name__ == "__main__":
    asyncio.run(main())