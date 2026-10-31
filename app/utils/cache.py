"""An in-memory TTL cache for async functions.

There were two of these. `sync_ttl_cache` was a copy of this one with
the arguments and the `await` removed, and it had no callers anywhere
in the repository — which is how it came to still contain the
`get(key) is not None` bug fixed below in f725d5d and nowhere else. A
second implementation nothing exercised was the only reason that bug
had somewhere to survive, so it is gone rather than repaired.
"""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from cachetools import TTLCache

P = ParamSpec('P')
R = TypeVar('R')


def async_ttl_cache(
    maxsize: int = 10_000, ttl: int = 600
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator: TTL cache keyed on all positional + keyword args.

    Usage::

        @async_ttl_cache(maxsize=10_000, ttl=600)
        async def expensive_lookup(org_id: str, user_id: str) -> str: ...


        # Manual invalidation:
        expensive_lookup.cache.clear()

    Every argument must be hashable. The key used to be
    ``str((args, kwargs))`` — a repr — which meant two distinct values
    that print the same shared one entry, and any object with the
    default address-based repr never hit at all. Hashing the arguments
    themselves uses the equality the caller defined, and raises
    ``TypeError`` on an unhashable argument instead of silently
    answering from the wrong entry.
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        # The store is built per decorated function rather than once
        # per ``async_ttl_cache(...)`` call. Two functions decorated
        # with the same bound decorator used to share one keyspace, so
        # a nullary one would serve the other's answer — only unreached
        # because nobody binds the decorator to a name. Typed on ``R``
        # here, where ``fn`` binds it, which is what lets a hit
        # type-check as that function's return value.
        cache: TTLCache[Any, R] = TTLCache(maxsize=maxsize, ttl=ttl)

        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # ``sorted`` on items is safe without a key function: dict
            # keys are unique, so tuple comparison never reaches the
            # values and never needs them to be orderable.
            key = (args, tuple(sorted(kwargs.items())))
            # ``in`` rather than ``get(key) is not None``.
            #
            # The old form could not cache a ``None`` result: it stored
            # one and then read it back as a miss, so a function returning
            # ``None`` re-ran every call while filling the cache with
            # entries nothing could use. ``_assert_active_member`` was
            # exactly that — and its docstring said "cached for 10
            # minutes" for as long as it was not.
            #
            # ``False``, ``0`` and ``[]`` were always cached correctly,
            # since they are not ``None``; only ``None`` fell through.
            if key in cache:
                return cache[key]
            result = await fn(*args, **kwargs)
            cache[key] = result
            return result

        # Stapled on so the documented `fn.cache.clear()` works. A
        # Protocol describing "callable, plus a cache" would let a
        # checker see it, but it cannot be expressed without naming the
        # signature twice, and one caller does not earn that.
        wrapper.cache = cache  # type: ignore[attr-defined]
        return wrapper

    return decorator
