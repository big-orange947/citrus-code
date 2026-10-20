import asyncio
from pathlib import Path

from mini_pi.agent.tool_runner import ToolRunner
from mini_pi.ai.types import ToolCall
from mini_pi.coding.tools.read import ReadTool
from mini_pi.coding.tools.write import WriteTool


async def main() -> None:
    cwd = Path.cwd()

    runner = ToolRunner(
        tools=[
            ReadTool(cwd=cwd),
            WriteTool(cwd=cwd),
        ]
    )

    print("已注册的工具：")

    for definition in runner.get_definitions():
        print("-", definition.name)

    file_path = "demo/hello.py"

    content = (
        "def add(a, b):\n"
        "\n"
        "    return a + b\n"
        "\n"
        "\n"
        "print(add(2, 3))\n"
    )

    write_call = ToolCall(
        id="call_write_1",
        name="write",
        arguments={
            "path": file_path,
            "content": content,
        },
    )

    print("\n开始写入：", file_path)

    write_result = await runner.execute(
        call=write_call,
    )

    for block in write_result.message.content:
        print(block.text)

    if write_result.message.is_error:
        print("写入失败，停止后续读取。")
        return

    print("写入详情：", write_result.details)

    read_call = ToolCall(
        id="call_read_1",
        name="read",
        arguments={
            "path": file_path,
        },
    )

    print("\n开始读取：", file_path)

    read_result = await runner.execute(
        call=read_call,
    )

    for block in read_result.message.content:
        print(block.text)

    if read_result.message.is_error:
        print("读取失败。")
        return

    print("读取详情：", read_result.details)


if __name__ == "__main__":
    asyncio.run(main())