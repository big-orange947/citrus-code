"""Offline session persistence and branch selection in a temporary directory."""

import time
from pathlib import Path
from tempfile import TemporaryDirectory

from citrus_code.ai.types import AssistantMessage, TextContent, UserMessage
from citrus_code.coding.session import SessionManager


def main() -> None:
    with TemporaryDirectory(prefix="citrus-code-session-") as directory:
        path = Path(directory) / "session.jsonl"
        session = SessionManager.create(cwd=Path.cwd(), file_path=path)
        session.append_model_change(provider="fake", model_id="fake-text")

        question_id = session.append_message(
            UserMessage(
                content="这个项目应该如何开始？",
                timestamp=int(time.time() * 1000),
            )
        )
        answer_a = session.append_message(
            AssistantMessage(
                content=[TextContent(text="方案 A：先实现模型调用层。")],
                api="fake",
                provider="fake",
                model="fake-text",
                usage=None,
                stop_reason="stop",
                timestamp=int(time.time() * 1000),
            )
        )

        # Return to the question and record an alternative answer.
        session.checkout(question_id)
        answer_b = session.append_message(
            AssistantMessage(
                content=[TextContent(text="方案 B：先定义统一消息类型。")],
                api="fake",
                provider="fake",
                model="fake-text",
                usage=None,
                stop_reason="stop",
                timestamp=int(time.time() * 1000),
            )
        )

        # Persist the selection even though no new message follows it.
        session.checkout(answer_a)
        print("已保存两个分支，当前选择方案 A。")
        print("文件中的记录行数：", len(path.read_text(encoding="utf-8").splitlines()))

        restored = SessionManager.load(path)
        context = restored.build_context()
        print("重新加载后的模型：", context.provider, context.model_id)
        print("全部树节点数：", len(restored.get_entries()))
        print("当前分支的消息数：", len(context.messages))
        print("恢复后的回答：", context.messages[-1].model_dump()["content"])

        # Continue using the reloaded manager as the sole writer.
        restored.checkout(answer_b)
        context = restored.build_context()
        print("切换到另一个分支：", context.messages[-1].model_dump()["content"])

    print("示例结束，临时会话文件已自动清理。")


if __name__ == "__main__":
    main()
