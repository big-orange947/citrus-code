import time

from citrus_code.agent.message_queue import PendingMessageQueue
from citrus_code.ai.types import UserMessage


def main() -> None:
    first = UserMessage(
        content="先调整登录方式",
        timestamp=int(time.time() * 1000),
    )

    second = UserMessage(
        content="注意保留原来的接口",
        timestamp=int(time.time() * 1000),
    )

    one_queue = PendingMessageQueue()

    all_queue = PendingMessageQueue(
        mode="all",
    )

    for message in (first, second):
        one_queue.enqueue(message)
        all_queue.enqueue(message)

    print("单条模式，第一次取出：")

    for message in one_queue.drain():
        print(message.content)

    print("是否还有消息：", one_queue.has_items())

    print("\n单条模式，第二次取出：")

    for message in one_queue.drain():
        print(message.content)

    print("是否还有消息：", one_queue.has_items())

    print("\n全部模式，一次取出：")

    for message in all_queue.drain():
        print(message.content)

    print("是否还有消息：", all_queue.has_items())


if __name__ == "__main__":
    main()