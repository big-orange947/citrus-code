import asyncio

from mini_pi.agent.cancellation import (
    AgentAbortedError,
    AbortSignal,
    wait_with_abort,
)


async def simulate_model() -> str:
    print("模型操作开始")

    try:
        await asyncio.sleep(10)

        return "模型回答完成"

    finally:
        print("模型任务执行清理")


async def request_abort(
    signal: AbortSignal,
) -> None:
    await asyncio.sleep(1)

    print("用户请求取消")

    signal.abort()


async def main() -> None:
    signal = AbortSignal()

    abort_request_task = asyncio.create_task(
        request_abort(signal)
    )

    try:
        response = await wait_with_abort(
            operation=simulate_model(),
            signal=signal,
        )

        print(response)

    except AgentAbortedError:
        print("本次操作已取消")

    finally:
        abort_request_task.cancel()

        await asyncio.gather(
            abort_request_task,
            return_exceptions=True,
        )


if __name__ == "__main__":
    asyncio.run(main())