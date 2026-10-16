import json
import time

from mini_pi.ai.providers.chat_completions import build_chat_request
from mini_pi.ai.types import (
    ModelContext,
    ModelOptions,
    ModelSpec,
    UserMessage,
)


def main() -> None:
    model = ModelSpec(
        id="deepseek-flash",
        name="DeepSeek V4.1 Flash",
        provider="deepseek",
        api="chat-completions",
        base_url="https://api.deepseek.com",
    )

    context = ModelContext(
        messages=[
            UserMessage(
                content="用一句话解释什么是 coding agent。",
                timestamp=int(time.time() * 1000),
            )
        ]
    )

    options = ModelOptions(
        max_output_tokens=1024,
    )

    body = build_chat_request(
        model=model,
        context=context,
        options=options,
    )

    print(json.dumps(body, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()