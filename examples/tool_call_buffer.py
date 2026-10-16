from mini_pi.ai.providers.chat_stream_types import ChatToolCallDelta
from mini_pi.ai.providers.tool_call_buffer import ToolCallBuffer


def main() -> None:
    raw_deltas = [
        {
            "index": 0,
            "id": "call_001",
            "type": "function",
            "function": {
                "name": "read",
                "arguments": '{"path":',
            },
        },
        {
            "index": 0,
            "function": {
                "arguments": '"README.md"',
            },
        },
        {
            "index": 0,
            "function": {
                "arguments": "}",
            },
        },
    ]

    buffer = ToolCallBuffer(
        tool_index=0,
        content_index=1,
    )

    for raw_delta in raw_deltas:
        delta = ChatToolCallDelta.model_validate(raw_delta)

        fragment = buffer.append(delta)

        print("本次片段：", fragment)
        print("累计参数：", buffer.arguments_text)

    tool_call = buffer.finish()

    print("完整调用：", tool_call.model_dump())


if __name__ == "__main__":
    main()