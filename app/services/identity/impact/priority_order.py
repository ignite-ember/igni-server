"""The super admin's one order of extensions, and how a loss by it is worded."""

from __future__ import annotations

from collections.abc import Iterable


class Priority:
    """The super admin's one order of extensions: first is highest.

    An extension the order does not name ranks below every one it does, by
    id. igni server always hands out the whole order, so that is only a
    machine with a development install.
    """

    def __init__(self, order: Iterable[str] = ()) -> None:
        self._rank = {extension_id: index for index, extension_id in enumerate(order)}

    def key(self, extension_id: str) -> tuple[int, str]:
        return (self._rank.get(extension_id, len(self._rank)), extension_id)

    def ordered(self, extension_ids: Iterable[str]) -> list[str]:
        return sorted(extension_ids, key=self.key)

    def above(self, first: str, second: str) -> bool:
        return self.key(first) < self.key(second)


def lower_than(winner: str | None) -> str:
    """How every loss by priority begins."""
    return f'lower priority than {winner}'


__all__ = ['Priority', 'lower_than']
