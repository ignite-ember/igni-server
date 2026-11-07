"""Signing and checking igni's tokens.

RS256 under a key the server generated for itself (see
`app.services.identity.key_ring`), so anybody holding the published key
set can check a token and nobody but this server can mint one. Every
token names its issuer (`iss`), who it is for (`aud`) and what kind it
is (`token_use`), and each check demands all three: a token minted for
CodeIndex is not a session here, and a refresh token is not an access
token anywhere.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import logging
from typing import Any

from pydantic import BaseModel

from app.config import settings
from app.security import jose
from app.services.identity.key_ring import KeyRing, SigningKeys

logger = logging.getLogger(__name__)

#: How long a session lasts — the portal's and the CLI's own token.
SESSION_LIFETIME = timedelta(days=30)


class TokenUse(StrEnum):
    """The `token_use` claim. Checked on every decode."""

    #: The portal's and the CLI's own token, for igni itself.
    SESSION = 'session'
    #: A person, for one registered client (`aud` = its client id).
    USER = 'user'
    #: A registered client acting as itself, for igni (`sub` = client id).
    SERVICE = 'service'
    #: Renews a `USER` token at igni's token endpoint.
    REFRESH = 'refresh'
    #: igni calling a registered service (erasure, subject access).
    IGNI = 'igni'
    #: An app — a producer of one organisation — acting as itself, for
    #: igni (`sub` = the app's client id, `kind` = `app`, no `group`).
    APP = 'app'
    #: A platform client (the cloud's gateway, node agent) acting as
    #: itself for the whole deployment (`sub` = client id, no `org`).
    PLATFORM = 'platform'
    #: A space — one end user of an app, a producer of its own — acting
    #: as itself (`sub` = the space id, `kind` = `space`, `app`,
    #: `external_id`; no `group`). `docs/service-identity.md` §17.
    SPACE = 'space'


#: The `kind` claim: which kind of producer a token is for.
PERSON = 'person'


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = 'bearer'


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """What a portal or CLI session token carries.

    `issued_at` is the `iat` in epoch seconds, and B5 compares it
    against `users.tokens_valid_after` — so a token minted before an
    operator withdrew somebody's sessions is refused. Kept as the
    seconds the token actually holds rather than converted to a datetime
    here, because converting in two places is how a comparison ends up
    an hour out.

    All-`None` means the token did not decode. Callers check `user_id`,
    which is what they did when this was an empty dict.
    """

    user_id: str | None = None
    org_id: str | None = None
    org_name: str | None = None
    issued_at: int | None = None
    #: The person's one group in `org_id` when the token was issued. A
    #: snapshot: what a person gets is decided from their group read at
    #: the time (`GET /v1/extensions` answers with it), never from this
    #: claim alone.
    group: str | None = None
    #: The personal API key this session was bought with, if it was
    #: (`docs/service-identity.md` §22). Such a session may do what the
    #: person may, except mint further credentials.
    api_key_id: str | None = None


class JWTService:
    """Mints and checks tokens with the process's key ring.

    Holds no state of its own: the ring is `SigningKeys`' and the issuer
    is `settings.public_base_url`, both read at the moment of use, so a
    key reload or a test's setting is seen without rebuilding anything.
    """

    #: Only what igni itself signs with. A token whose header names
    #: anything else — `none`, or `HS256` keyed with the public key — is
    #: refused before any key is looked up.
    ALGORITHMS = ['RS256']

    def __init__(self, ring: Callable[[], KeyRing] = SigningKeys.ring) -> None:
        self._ring = ring

    @property
    def issuer(self) -> str:
        """This server's public URL — every token's `iss`."""
        return settings.public_base_url.rstrip('/')

    # ── Sessions: igni's own token ──────────────────────────────────

    def create_token(
        self,
        user_id: str,
        expires_delta: timedelta | None = None,
        org_id: str | None = None,
        org_name: str | None = None,
        group: str | None = None,
        incarnation: str | None = None,
        api_key_id: str | None = None,
    ) -> str:
        """A session for `user_id`, optionally pinned to an organisation and carrying its group there.

        *incarnation* — the membership a VM's token is for (§11.3) — is
        carried as `inc`, so the gateway can match it to the feed.
        *api_key_id* — the personal API key the session was exchanged for
        (§22) — is carried as `api_key`.
        """
        extra: dict[str, object] = {'kind': PERSON}
        if incarnation:
            extra['inc'] = incarnation
        if api_key_id:
            extra['api_key'] = api_key_id
        if org_id:
            extra['org'] = org_id
            if group:
                extra['group'] = group
        if org_name:
            extra['org_name'] = org_name
        return self.sign(
            subject=user_id,
            audience=self.issuer,
            use=TokenUse.SESSION,
            lifetime=expires_delta or SESSION_LIFETIME,
            claims=extra,
        )

    def decode_token(self, token: str) -> TokenClaims:
        """A session's claims, or an all-`None` `TokenClaims`."""
        payload = self.verify(token, audience=self.issuer, use=TokenUse.SESSION)
        if payload is None:
            return TokenClaims()
        sub = payload.get('sub')
        issued_at = payload.get('iat')
        return TokenClaims(
            user_id=str(sub) if sub is not None else None,
            org_id=str(payload['org']) if 'org' in payload else None,
            org_name=str(payload['org_name']) if 'org_name' in payload else None,
            issued_at=int(issued_at) if issued_at is not None else None,
            group=str(payload['group']) if payload.get('group') else None,
            api_key_id=str(payload['api_key']) if payload.get('api_key') else None,
        )

    def decode_user_id(self, token: str) -> str | None:
        """Convenience: decode and return just the user_id, or None."""
        return self.decode_token(token).user_id

    # ── Any token ────────────────────────────────────────────────────

    def sign(
        self,
        *,
        subject: str,
        audience: str,
        use: TokenUse,
        lifetime: timedelta,
        claims: dict[str, object] | None = None,
    ) -> str:
        """Sign a token. The registered claims are always set here."""
        ring = self._ring()
        now = datetime.now(timezone.utc)
        payload: dict[str, object] = dict(claims or {})
        payload.update(
            {
                'iss': self.issuer,
                'sub': subject,
                'aud': audience,
                'token_use': str(use),
                'iat': now,
                # Milliseconds too: a revocation (docs/service-identity.md §21)
                # is exact to them, so a token issued just after a removal in
                # the same second is not refused with the ones before it.
                'iat_ms': int(now.timestamp() * 1000),
                'exp': now + lifetime,
            }
        )
        return jose.encode(payload, ring.private_pem(), algorithm='RS256', headers={'kid': ring.kid})

    def verify(self, token: str, *, audience: str, use: TokenUse) -> dict[str, Any] | None:
        """The payload of a valid token of this kind for `audience`, or `None`."""
        try:
            header = jose.unverified_header(token)
        except jose.TokenError:
            return None
        key = self._ring().verifying_key(str(header.get('kid', '')))
        if key is None:
            return None
        try:
            payload: dict[str, Any] = jose.decode(
                token,
                key.as_dict(),
                algorithms=self.ALGORITHMS,
                audience=audience,
                issuer=self.issuer,
                require=('iat', 'exp', 'sub'),
            )
        except jose.TokenError:
            return None
        if payload.get('token_use') != str(use):
            return None
        return payload

    # ``resolve_user_id`` used to live here: decode, 401 on a missing
    # claim, return the id. Removed rather than kept, because its whole
    # job was to produce a subject nobody had verified still exists — and
    # it was the convenient way to do half of an authorisation check
    # while reading like the whole of one. Both callers now use
    # ``app.security.subject.resolve_existing_user_id``, which cannot be
    # used to do half. F122.
