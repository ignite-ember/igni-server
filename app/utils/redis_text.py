"""One place that turns a Redis reply into `str`.

Every client in this codebase is built with `decode_responses=True`, so
replies arrive as `str` at runtime. redis-py's type stubs cannot express
that — `decode_responses` is a constructor argument, not a type parameter
— so they describe reads as `bytes | str`, and after the redis 8.x upgrade
mypy reported ten errors where that union met a `str` parameter.

Two ways to silence that. `cast` asserts the stubs are wrong and produces
`b'...'` in an f-string the day somebody builds a client without
`decode_responses`. This decodes instead, so the annotation is true
whichever way the client was constructed and the failure mode is
impossible rather than merely unlikely.

Only for keys and values that really are text. A reply holding binary
(a pickled blob, an image) must not come through here.
"""

from __future__ import annotations

from typing import overload


@overload
def as_str(value: bytes | str) -> str: ...


@overload
def as_str(value: None) -> None: ...


def as_str(value: bytes | str | None) -> str | None:
    """Decode a Redis reply to `str`, passing `None` through.

    `None` survives because a missing key is a normal answer from `get`
    and `mget`, and callers distinguish it from an empty string.
    """
    if isinstance(value, bytes):
        return value.decode()
    return value
