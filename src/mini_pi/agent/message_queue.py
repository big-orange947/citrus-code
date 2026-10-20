from typing import Literal

from ..ai.types import Message


QueueMode = Literal["one-at-a-time", "all"]


class PendingMessageQueue:
    def __init__(
        self,
        mode: QueueMode = "one-at-a-time",
    ) -> None:
        if mode not in ("one-at-a-time", "all"):
            raise ValueError(
                f"Unsupported queue mode: {mode}"
            )

        self._mode = mode
        self._messages: list[Message] = []

    def enqueue(self, message: Message) -> None:
        self._messages.append(message)

    def has_items(self) -> bool:
        return bool(self._messages)

    def peek(self) -> list[Message]:
        if self._mode == "all":
            return self._messages.copy()

        return self._messages[:1]

    def drain(self) -> list[Message]:
        messages = self.peek()

        self._messages = self._messages[
            len(messages):
        ]

        return messages

    def clear(self) -> None:
        self._messages.clear()