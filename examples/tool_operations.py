"""Inject recording file operations; no model or external commands are used."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from citrus_code.agent.tool_runner import ToolRunner
from citrus_code.ai.types import TextContent, ToolCall
from citrus_code.coding.tools import (
    LocalFileOperations,
    LocalSearchOperations,
    LocalShellOperations,
    ToolOperations,
    create_tools,
)


class RecordingFileOperations(LocalFileOperations):
    def read_bytes(self, path: str) -> bytes:
        print(f"[operations.read_bytes] {path}")
        return super().read_bytes(path)

    def write_bytes(self, path: str, content: bytes) -> None:
        print(f"[operations.write_bytes] {path}: {len(content)} bytes")
        super().write_bytes(path, content)


async def main() -> None:
    with TemporaryDirectory(prefix="citrus-code-operations-") as directory:
        files = RecordingFileOperations()
        operations = ToolOperations(
            files=files,
            shell=LocalShellOperations(),
            search=LocalSearchOperations(files),
        )
        runner = ToolRunner(create_tools(
            Path(directory), ("write", "edit", "read", "ls"), operations=operations,
        ))
        calls = [
            ToolCall(id="write-1", name="write", arguments={
                "path": "hello.py", "content": "print('hello')\n",
            }),
            ToolCall(id="edit-1", name="edit", arguments={
                "path": "hello.py",
                "edits": [{"oldText": "'hello'", "newText": "'Citrus Code'"}],
            }),
            ToolCall(id="read-1", name="read", arguments={"path": "hello.py"}),
            ToolCall(id="ls-1", name="ls", arguments={}),
        ]
        for call in calls:
            result = await runner.execute(call)
            for block in result.message.content:
                if isinstance(block, TextContent):
                    print(block.text)
            if result.message.is_error:
                raise RuntimeError(f"Example tool failed: {call.name}")


if __name__ == "__main__":
    asyncio.run(main())
