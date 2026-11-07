"""Redis-backed storage for OAuth CSRF state tokens.

**Single-use is the whole contract.** A state token proves that the
callback now arriving belongs to a flow this server started, and for
OIDC it also carries the nonce and the PKCE verifier that stop an
injected authorization code from being exchanged. A token that can be
consumed twice is a token that defends against neither on the second
attempt.

**Neither the token nor what it carries is in Redis as given** (F151).
The key is the state token's SHA-256 — it is ``token_urlsafe(32)`` — and
the record is encrypted under ``SECRET_ENCRYPTION_KEY``, because for OIDC
it holds the PKCE verifier. See ``app/db/redis/sealed.py``.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel
import redis.asyncio as aioredis

from app.config import settings
from app.db.redis.keys import key
from app.db.redis.sealed import digest, seal, unseal
from app.services.secrets import SecretDecryptError
from app.utils.redis_text import as_str

logger = logging.getLogger(__name__)

#: Long enough for a person to take their time at the provider's consent
#: screen.
STATE_TTL_SECONDS = 600


class OAuthStateDB:
    """Stores and claims OAuth state tokens, each with the record its flow carries.

    The store is generic over the record: each flow names its own model
    (`OidcSignInState`, `SocialSignInState`) and the claim reads the
    record back as that model, so no flow's shape is a loose dict.
    """

    @staticmethod
    def redis_key(state: str) -> str:
        """Where a state token's record lives: under its digest, never itself."""
        return key('oauth', 'state', digest(state))

    def _redis(self) -> aioredis.Redis:  # type: ignore[type-arg]
        client: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[assignment]
        return client

    async def store(self, state: str, record: BaseModel) -> None:
        """Store `record` under `state`, sealed, for `STATE_TTL_SECONDS`.

        Defaults are left out, so an absent optional field stays absent
        rather than coming back as a stored empty value.
        """
        sealed = seal(record.model_dump_json(by_alias=True, exclude_defaults=True))
        async with self._redis() as client:
            await client.set(self.redis_key(state), sealed, ex=STATE_TTL_SECONDS)

    async def claim[R: BaseModel](self, state: str, shape: type[R]) -> R | None:
        """Claim a state token, exactly once, and read its record as `shape`.

        `None` if the token is unknown, expired, already claimed, or
        sealed under a key since removed. Raises `ValidationError` if the
        record is not a `shape` — another flow's state, which the caller
        refuses in its own terms.

        `GETDEL`, not `GET` then `DELETE`: the two-step version let two
        callbacks carrying the same token both read it before either
        deleted it, and for OIDC that hands a second consumer a working
        nonce and PKCE verifier for a flow that is not theirs.
        """
        async with self._redis() as client:
            raw = await client.getdel(self.redis_key(state))
        if raw is None:
            return None
        try:
            record = unseal(as_str(raw))
        except SecretDecryptError:
            # The token is spent either way; the flow it belonged to has
            # to start again.
            logger.error('An OAuth state token was found but will not decrypt; the sign-in must start again')
            return None
        return shape.model_validate_json(record)
