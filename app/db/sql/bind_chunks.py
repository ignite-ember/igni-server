"""Long id lists cut into slices one statement can bind."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

#: Ids per statement, well under the driver's limit on bind parameters.
CHUNK = 5000


def chunks(values: Sequence[str]) -> Iterable[Sequence[str]]:
    """*values* in slices small enough for one statement's parameters."""
    for start in range(0, len(values), CHUNK):
        yield values[start : start + CHUNK]
