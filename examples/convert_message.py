import json
import time

from citrus_code.ai.providers.chat_completions import to_chat_message
from citrus_code.ai.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
)


def main() -> None:
    message = AssistantMessage(
        content=[
            TextContent(text="我先读取项目说明。"),
            ToolCall(
                id="call_001",
                name="read",
                arguments={"path": "README.md"},
            ),
        ],
        api="chat-completions",
        provider="deepseek",
        model="deepseek-flash",
        usage=None,
        stop_reason="toolUse",
        timestamp=int(time.time() * 1000),
    )

    converted = to_chat_message(message)

    print(
        json.dumps(
            converted,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()