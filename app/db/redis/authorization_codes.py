"""One-time codes for the authorization-code flow, and spent assertions.

Both are single-use and short-lived, which is Redis's shape rather than
Postgres's. Both are about a sign-in in progress, not about anybody's
organisation, so they live in the server's own Redis.

An authorization code is stored under its SHA-256, never as itself
(F151): a copy of Redis taken while one is live must not be redeemable
at the token endpoint. What it stands for — client, redirect URI, PKCE
challenge, user, organisation — holds no secret and is stored as is.
"""

from __future__ import annotations

import secrets

from pydantic import BaseModel
import redis.asyncio as aioredis

from app.config import settings
from app.db.redis.keys import key
from app.db.redis.sealed import digest

#: Long enough for a browser to follow a redirect and the service behind
#: it to post the code back; short enough that a code seen in a log or a
#: referrer is already dead.
CODE_TTL_SECONDS = 60


class PendingAuthorization(BaseModel):
    """What a code stands for, until the client redeems it."""

    client_id: str
    redirect_uri: str
    code_challenge: str
    user_id: str
    org_id: str
    #: The `iat` of the portal session that approved it, so withdrawing
    #: that session kills a code still in flight.
    session_issued_at: int


class _RedisStore:
    async def _redis(self) -> aioredis.Redis:  # type: ignore[type-arg]
        client: aioredis.Redis = aioredis.from_url(  # type: ignore[assignment]
            settings.redis_url, decode_responses=True
        )
        return client


class AuthorizationCodeDB(_RedisStore):
    async def issue(self, pending: PendingAuthorization) -> str:
        code = secrets.token_urlsafe(32)
        client = await self._redis()
        try:
            await client.set(key('oauth_code', digest(code)), pending.model_dump_json(), ex=CODE_TTL_SECONDS)
        finally:
            await client.aclose()
        return code

    async def redeem(self, code: str) -> PendingAuthorization | None:
        """The authorization, consumed. `GETDEL`, so two redemptions cannot both win."""
        if not code:
            return None
        client = await self._redis()
        try:
            raw = await client.getdel(key('oauth_code', digest(code)))
        finally:
            await client.aclose()
        return PendingAuthorization.model_validate_json(raw) if raw else None


class SpentAssertionDB(_RedisStore):
    """`jti`s of client assertions already used.

    A client assertion is a bearer credential for as long as it is
    valid, so one seen in transit could be replayed for a token of its
    own. Each `jti` is accepted once, and remembered until the assertion
    would have expired anyway.
    """

    async def spend(self, *, client_id: str, jti: str, ttl_seconds: int) -> bool:
        """True the first time; False if this assertion was used before."""
        client = await self._redis()
        try:
            first = await client.set(key('assertion', client_id, jti), '1', ex=max(ttl_seconds, 1), nx=True)
        finally:
            await client.aclose()
        return bool(first)
