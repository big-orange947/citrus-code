"""Local tool walkthrough in a temporary directory; no model or API key needed."""

import argparse
import asyncio
import tempfile
from pathlib import Path

from mini_pi.agent.tool_runner import ToolRunner
from mini_pi.ai.types import ToolCall
from mini_pi.coding.tools import create_all_tools


async def main(shell: str | None) -> None:
    with tempfile.TemporaryDirectory(prefix="mini-pi-example-") as directory:
        cwd = Path(directory)
        runner = ToolRunner(tools=list(create_all_tools(cwd).values()))
        print("工具：", ", ".join(tool.name for tool in runner.get_definitions()))
        print("临时工作目录：", cwd)

        calls = [
            ToolCall(id="write_1", name="write", arguments={
                "path": "demo/hello.py",
                "content": "def add(a, b):\n    return a + b\n\nprint(add(2, 3))\n",
            }),
            ToolCall(id="edit_1", name="edit", arguments={
                "path": "demo/hello.py",
                "edits": [
                    {"oldText": "return a + b", "newText": "return a * b"},
                    {"oldText": "print(add(2, 3))", "newText": "print(add(4, 5))"},
                ],
            }),
            ToolCall(id="read_1", name="read", arguments={"path": "demo/hello.py"}),
            ToolCall(id="ls_1", name="ls", arguments={}),
            ToolCall(id="find_1", name="find", arguments={"pattern": "*.py"}),
            ToolCall(id="grep_1", name="grep", arguments={"pattern": "return", "glob": "*.py"}),
        ]
        if shell is not None:
            command = "printf 'mini-pi shell ready\\n'" if shell == "bash" else "Write-Output 'mini-pi shell ready'"
            calls.append(ToolCall(id="shell_1", name=shell, arguments={"command": command, "timeout": 10}))

        for call in calls:
            print(f"\n[{call.name}] {call.arguments}")
            result = await runner.execute(call)
            for block in result.message.content:
                print(block.text)
            if result.message.is_error:
                print("工具执行失败，示例停止。")
                return
            if call.name == "edit":
                print(result.details["diff"])

    print("\n演示结束，临时目录已清理。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shell", choices=("bash", "powershell"))
    args = parser.parse_args()
    asyncio.run(main(args.shell))
