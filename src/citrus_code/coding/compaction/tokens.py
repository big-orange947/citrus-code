"""Approximate token accounting; provider usage is the preferred baseline."""

import json
import math

from ...ai.types import ImageContent, Message, ModelContext, SystemMessage, ToolCall


CONTEXT_WINDOW = 1_000_000
COMPACTION_THRESHOLD = 600_000
KEEP_RECENT_ROUNDS = 5
KEEP_RECENT_TOKENS = 100_000


def estimate_text(text: str) -> int:
    # Non-ASCII text gets a larger allowance than the English chars/4 rule.
    ascii_chars = sum(character.isascii() for character in text)
    return math.ceil(ascii_chars / 4 + (len(text) - ascii_chars) * 2)


def estimate_message(message: Message) -> int:
    tokens = 8  # Approximate role and message framing overhead.
    if isinstance(message.content, str):
        return tokens + estimate_text(message.content)
    for block in message.content:
        if isinstance(block, ImageContent):
            # An estimate only; do not count the base64 payload as ordinary text.
            tokens += 4096
        elif isinstance(block, ToolCall):
            tokens += estimate_text(block.name + json.dumps(block.arguments, ensure_ascii=False))
        else:
            tokens += estimate_text(block.text)
    return tokens


def estimate_request(context: ModelContext) -> int:
    tools = json.dumps(
        [tool.model_dump(mode="json") for tool in context.tools], ensure_ascii=False,
    )
    return sum(estimate_message(message) for message in context.messages) + estimate_text(tools)


def replace_system_messages(messages: list[Message], request: ModelContext) -> ModelContext:
    return ModelContext(
        messages=[message for message in request.messages if isinstance(message, SystemMessage)]
        + [message for message in messages if not isinstance(message, SystemMessage)],
        tools=request.tools,
    )
