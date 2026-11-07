"""Redis-backed storage for OAuth CSRF state tokens.

**Single-use is the whole contract.** A state token proves that the
callback now arriving belongs to a flow this server started, and for
OIDC it also carries the nonce and the PKCE verifier that stop an
injected authorization code from being exchanged. A token that can be
consumed twice is a token that defends against neither on the second
attempt.

**Neither the token nor what it carries is in Redis as given** (F151).
The key is the state token's SHA-256 — it is ``token_urlsafe(32)`` — and
metadata is encrypted under ``SECRET_ENCRYPTION_KEY``, because for OIDC
it holds the PKCE verifier. See ``app/db/redis/sealed.py``.
"""

import json
import logging

import redis.asyncio as aioredis

from app.config import settings
from app.db.redis.sealed import digest, seal, unseal
from app.services.secrets import SecretDecryptError
from app.utils.redis_text import as_str

logger = logging.getLogger(__name__)

_PREFIX = 'oauth:state:'
_TTL_SECONDS = 600  # 10 minutes

#: Stored for a token that carries no metadata. Not a secret, so not sealed.
_NO_METADATA = '1'


class OAuthStateDB:
    """Stores and validates OAuth state tokens in Redis.

    The metadata stays an untyped `dict[str, str]` on purpose. Each
    flow puts different things in it — OIDC a nonce and a PKCE
    verifier, the social sign-in path a `returnTo` — so this layer is a
    keyed store and the shape belongs to each caller: `OidcSignInState`
    and `SocialSignInState` name the two.

    Redis is opened per operation rather than pooled, matching
    `auth_handoff`, `code_throttle` and the OTP store. Worth changing;
    not worth changing one of five in isolation.
    """

    async def _get_redis(self) -> aioredis.Redis:  # type: ignore[type-arg]
        r: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[assignment]
        return r

    async def store(self, state: str, metadata: dict[str, str] | None = None) -> None:
        """Store a state token with TTL and optional metadata."""
        r = await self._get_redis()
        try:
            value = seal(json.dumps(metadata)) if metadata else _NO_METADATA
            await r.set(f'{_PREFIX}{digest(state)}', value, ex=_TTL_SECONDS)
        finally:
            await r.aclose()

    async def validate_and_consume(self, state: str) -> dict[str, str] | None:
        """Claim a state token, exactly once. `None` if it was not there.

        `GETDEL`, not `GET` then `DELETE`. The two-step version let two
        callbacks carrying the same token both read it before either
        deleted it, so both proceeded — demonstrated against the real
        Redis before this was changed: eight concurrent consumers of
        one token, and **four** of them were handed it.

        That is the single-use guarantee failing in the way that
        matters. For OIDC the token carries the nonce and the PKCE
        verifier, so a second consumer gets a working set of secrets
        for a flow that is not theirs.

        One command rather than a MULTI/EXEC pair because it is one
        command: available since Redis 6.2, and the deployment runs
        7.4.
        """
        r = await self._get_redis()
        try:
            raw = await r.getdel(f'{_PREFIX}{digest(state)}')
        finally:
            await r.aclose()
        if not raw:
            return None
        raw = as_str(raw)
        if raw != _NO_METADATA:
            try:
                raw = unseal(raw)
            except SecretDecryptError:
                # Sealed under a key since removed, or written in plaintext
                # before F151. The token is spent either way; the flow it
                # belonged to has to start again.
                logger.error('An OAuth state token was found but will not decrypt; the sign-in must start again')
                return None
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        # A token stored with no metadata is the sentinel `'1'`, which
        # parses as the **integer** 1 — not a decode error, so this
        # used to return it. The annotation says `dict | None`, and
        # the social sign-in callback does `metadata.get('returnTo')`
        # one line after reading it:
        #
        #     AttributeError: 'int' object has no attribute 'get'
        #
        # Reached by signing in with Google without
        # a `returnTo` — the redirect stores `None`, so the sentinel is
        # what comes back. Reproduced through `OAuthService` before
        # being fixed here.
        #
        # Present-and-empty is a real answer and is not absent: the
        # token was valid, it just carried nothing.
        return parsed if isinstance(parsed, dict) else {}
