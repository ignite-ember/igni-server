"""Whether the caller is still there, as the chat proxy needs to know it."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any, Protocol


class CallerPresence(Protocol):
    """Cancels work when the caller leaves; the api layer's `ClientConnection` is one."""

    async def unless_it_closes[T](self, work: Coroutine[Any, Any, T]) -> T | None:
        """Run *work*, cancelling it if the caller leaves first; None when they did."""
        ...
