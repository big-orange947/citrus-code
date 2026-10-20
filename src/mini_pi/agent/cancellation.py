import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


class AgentAbortedError(Exception):
    """The current Agent run was intentionally abroted."""


class AbortSignal:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    def abort(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_aborted(self) -> None:
        if self.aborted:
            raise AgentAbortedError(
                "The Agent run was aborted"
            )



async def wait_with_abort(
        operation: Awaitable[T],
        signal: AbortSignal,
) -> T:
    operation_task = asyncio.ensure_future(operation)
    abort_task = asyncio.create_task(signal.wait())

    try:
        signal.raise_if_aborted()

        completed, _ = await asyncio.wait(
            (operation_task, abort_task),
            return_when=asyncio.FIRST_COMPLETED,
        )

        if operation_task in completed:
            return operation_task.result()

        raise AgentAbortedError(
            "The Agent run was aborted"
        )
    finally:
        if not operation_task.done():
            operation_task.cancel()

        if not abort_task.done():
            abort_task.cancel()

        await asyncio.gather(
            operation_task,
            abort_task,
            return_exceptions=True,
        )
