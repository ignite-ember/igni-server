"""One-time codes that hand a freshly issued token to the browser.

F107. An OAuth or SSO callback used to redirect to
``/login?token=<jwt>``, which puts a working credential in:

* the browser's history, where it stays after sign-out;
* the ``Referer`` header of anything that page loads or links to;
* the access log of whatever proxies the portal — and F102 made those
  logs machine-readable and shippable, which is good for everything
  except this;
* any extension with tab access.

None of that is exotic. It is the reason the OAuth 2.0 security
best-current-practice tells browser clients to exchange a short-lived
code rather than receive the token directly.

So the callback now redirects with a code, and the portal posts it back
to ``/v1/auth/exchange`` to receive the token in a response body. The
code is still in the URL and still in the history — but it is worthless
seconds later and worthless after one use, which is the whole point.

## Why not a cookie

An ``HttpOnly`` cookie would keep the token out of JavaScript entirely
and is the better answer for a pure web app. It is not this app: the CLI
sign-in flow hands the token to a process on the developer's machine, so
something has to be able to read it. A cookie would also need CSRF
protection on every mutating endpoint, which is a larger change than the
problem being fixed here.

## Why a separate store from the OAuth state

Both are single-use Redis keys with a TTL, and reusing the state store
would mean a CSRF state token and a token-bearing handoff code live in
one namespace, redeemable through one another's endpoint. Different
secrets, different lifetimes, different blast radius: separate prefix.

## What Redis holds

Neither the code nor the token (F151). The key is the code's SHA-256 and
the value is the token encrypted under ``SECRET_ENCRYPTION_KEY``, so a
snapshot taken inside the 30 seconds yields neither something to post to
``/v1/auth/exchange`` nor the bearer token itself. See
``app/db/redis/sealed.py``.
"""

from __future__ import annotations

import logging
import secrets

import redis.asyncio as aioredis

from app.config import settings
from app.db.redis.sealed import digest, seal, unseal
from app.services.secrets import SecretDecryptError

logger = logging.getLogger(__name__)

_PREFIX = 'auth_handoff:'

#: Long enough for the browser to follow one redirect and post once.
#:
#: 30 seconds rather than the state store's 10 minutes: nobody waits at
#: this step. The window is the interval in which a code observed in a
#: log or a referrer is still worth something.
_TTL_SECONDS = 30


class AuthHandoffDB:
    """Issues and redeems the one-time codes."""

    async def _redis(self) -> aioredis.Redis:  # type: ignore[type-arg]
        client: aioredis.Redis = aioredis.from_url(  # type: ignore[assignment]
            settings.redis_url, decode_responses=True
        )
        return client

    async def issue(self, token: str) -> str:
        """Store ``token`` under a fresh code and return the code."""
        code = secrets.token_urlsafe(32)
        client = await self._redis()
        try:
            await client.set(f'{_PREFIX}{digest(code)}', seal(token), ex=_TTL_SECONDS)
        finally:
            await client.aclose()
        return code

    async def redeem(self, code: str) -> str | None:
        """Return the token for ``code`` and invalidate it. ``None`` if unknown.

        ``GETDEL``, which is atomic — not ``GET`` then ``DELETE``. The
        two-step version lets two requests arriving together both read
        the value before either deletes it, so a code observed in flight
        could be redeemed alongside the legitimate exchange. The OAuth
        state store next door still does it in two steps; for a CSRF
        nonce that is survivable, and for something that hands out a
        token it is not.
        """
        if not code:
            return None

        client = await self._redis()
        try:
            raw = await client.getdel(f'{_PREFIX}{digest(code)}')
        finally:
            await client.aclose()

        if raw is None:
            return None
        # `decode_responses=True` means this is already a str; the stub
        # is honest that the client can be configured either way.
        sealed = raw if isinstance(raw, str) else raw.decode()
        try:
            return unseal(sealed)
        except SecretDecryptError:
            # Sealed under a key since removed, or written before F151 in
            # plaintext. Either way the code is spent and the user signs
            # in again; saying so is all that is left to do.
            logger.error('A sign-in handoff code was found but its token will not decrypt; it was discarded')
            return None
