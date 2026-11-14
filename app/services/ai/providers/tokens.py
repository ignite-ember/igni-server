"""A cloud credential that expires, where it comes from, and the cache that renews it.

A `TokenSource` is one way of getting a credential for one backend — the
server's own identity, an impersonated account, a stored key — built
from the backend's auth settings and secret. `CachedToken` holds what a
source last gave and asks again `REFRESH_MARGIN_SECONDS` before it runs
out, or at once after `invalidate`, so a refused token costs one renewal
rather than a failed request.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import time
from typing import Generic, TypeVar

from app.services.ai.providers.kinds import BackendKind

#: Renew a credential this many seconds before it expires.
REFRESH_MARGIN_SECONDS = 300

T = TypeVar('T')


@dataclass(frozen=True)
class AccessToken(Generic[T]):
    """A credential and the moment it stops working, in epoch seconds."""

    value: T
    expires_at: float


class TokenSource(ABC, Generic[T]):
    """One backend's way of getting a fresh credential, from its auth settings and secret."""

    def __init__(self, *, settings: Mapping[str, str], secret: str | None) -> None:
        self._settings = settings
        self._secret = secret

    @abstractmethod
    async def fetch(self) -> AccessToken[T]:
        """A fresh credential. Raises whatever the cloud's SDK raises when it gives none."""


class CredentialUnavailable(Exception):
    """The server could not get a credential for a cloud backend.

    The message names the kind and never the credential: it reaches logs
    and, through `UpstreamCredentialsUnavailable`, a caller's error.
    """

    def __init__(self, kind: BackendKind) -> None:
        self.kind = kind
        super().__init__(f'No credentials for the {kind.value} backend')


class CachedToken(Generic[T]):
    """One backend's credential, fetched when missing or about to run out."""

    def __init__(self, source: TokenSource[T], *, clock: Callable[[], float] = time.time) -> None:
        self._source = source
        self._clock = clock
        self._token: AccessToken[T] | None = None
        self._lock = asyncio.Lock()

    async def value(self) -> T:
        """The current credential, renewed first if it is within the margin of expiring."""
        async with self._lock:
            if self._token is None or self._token.expires_at - REFRESH_MARGIN_SECONDS <= self._clock():
                self._token = await self._source.fetch()
            return self._token.value

    def invalidate(self) -> None:
        """Forget the credential, so the next `value` fetches a new one."""
        self._token = None
