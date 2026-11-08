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
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import logging

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.security import jose
from app.services.auth.token_claims import CustomClaims, TokenClaims, VerifiedClaims
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


class JWTService:
    """Mints and checks tokens with the process's key ring.

    Holds no state of its own: the ring is `SigningKeys`' and the issuer
    is `settings.public_base_url`, both read at the moment of use, so a
    key reload or a test's setting is seen without rebuilding anything.
    """

    #: Only what igni itself signs with. A token whose header names
    #: anything else — `none`, or `HS256` keyed with the public key — is
    #: refused before any key is looked up.
    ALGORITHMS = ('RS256',)

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
        *,
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
        (§22) — is carried as `api_key`. Empty values are left out.
        """
        claims = CustomClaims(
            kind=PERSON,
            org=org_id or None,
            group=(group or None) if org_id else None,
            org_name=org_name or None,
            inc=incarnation or None,
            api_key=api_key_id or None,
        )
        return self.sign(
            subject=user_id,
            audience=self.issuer,
            use=TokenUse.SESSION,
            lifetime=expires_delta or SESSION_LIFETIME,
            claims=claims,
        )

    def decode_token(self, token: str) -> TokenClaims:
        """A session's claims, or an all-`None` `TokenClaims`."""
        claims = self.verify(token, audience=self.issuer, use=TokenUse.SESSION)
        return TokenClaims() if claims is None else TokenClaims.of(claims)

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
        claims: CustomClaims | None = None,
    ) -> str:
        """Sign a token. The registered claims are always set here."""
        ring = self._ring()
        now = datetime.now(timezone.utc)
        payload = claims.payload() if claims else {}
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

    def verify(self, token: str, *, audience: str, use: TokenUse) -> VerifiedClaims | None:
        """The claims of a valid token of this kind for `audience`, or `None`."""
        try:
            header = jose.unverified_header(token)
        except jose.TokenError:
            return None
        key = self._ring().verifying_key(str(header.get('kid', '')))
        if key is None:
            return None
        try:
            payload = jose.decode(
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
        try:
            return VerifiedClaims.model_validate(payload)
        except ValidationError:
            return None
