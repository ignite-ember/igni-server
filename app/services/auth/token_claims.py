"""What igni's tokens carry, as models rather than dicts.

`CustomClaims` is everything a caller of `JWTService.sign` may add next
to the registered claims, which `sign` always sets itself.
`VerifiedClaims` is what `JWTService.verify` hands back: the same claims
plus the subject and issue time every token is required to have.
`TokenClaims` is the narrower view of a portal or CLI session.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CustomClaims(BaseModel):
    """The claims beyond `iss`/`sub`/`aud`/`token_use`/`iat`/`exp`. Unset ones are left out of the token."""

    model_config = ConfigDict(frozen=True, extra='ignore')

    #: The organisation the token is for.
    org: str | None = None
    org_name: str | None = None
    org_role: str | None = None
    #: The person's one group in `org` when the token was issued.
    group: str | None = None
    #: Which kind of producer the token is for (`person`, `app`, `space`).
    kind: str | None = None
    #: A person's or an app's display name.
    name: str | None = None
    email: str | None = None
    super_admin: bool | None = None
    #: The membership a VM's token is for (§11.3).
    inc: str | None = None
    #: The personal API key a session was exchanged for (§22).
    api_key: str | None = None
    #: A space's app, and its id in that app (§17).
    app: str | None = None
    external_id: str | None = None
    #: A service token's scope when it names no organisation (§21).
    scope: str | None = None
    #: The client a token was issued to.
    azp: str | None = None
    #: The sign-in a refresh token belongs to, and the token's own id.
    grant: str | None = None
    jti: str | None = None

    def payload(self) -> dict[str, object]:
        """The claims as they go into the token: only the ones that are set."""
        return self.model_dump(exclude_none=True)


class VerifiedClaims(CustomClaims):
    """A verified token's claims. `sub` and `iat` are required of every token igni accepts."""

    sub: str
    #: Issue time in epoch seconds.
    iat: int


class TokenClaims(BaseModel):
    """What a portal or CLI session token carries.

    `issued_at` is the `iat` in epoch seconds, and B5 compares it
    against `users.tokens_valid_after` — so a token minted before an
    operator withdrew somebody's sessions is refused. Kept as the
    seconds the token actually holds rather than converted to a datetime
    here, because converting in two places is how a comparison ends up
    an hour out.

    All-`None` means the token did not decode. Callers check `user_id`.
    """

    model_config = ConfigDict(frozen=True)

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

    @classmethod
    def of(cls, claims: VerifiedClaims) -> TokenClaims:
        return cls(
            user_id=claims.sub,
            org_id=claims.org,
            org_name=claims.org_name,
            issued_at=claims.iat,
            group=claims.group or None,
            api_key_id=claims.api_key or None,
        )
