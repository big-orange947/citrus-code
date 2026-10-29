"""Async generator cleanup shared by Python 3.9 and newer interpreters."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol, TypeVar


class _AsyncClosable(Protocol):
    async def aclose(self) -> None: ...


_T = TypeVar("_T", bound=_AsyncClosable)


@asynccontextmanager
async def aclosing(value: _T) -> AsyncIterator[_T]:
    """Close the generator on normal exit, early break, error or cancellation."""
    try:
        yield value
    finally:
        await value.aclose()
