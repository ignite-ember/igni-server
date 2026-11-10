"""What a client posts to the token and revocation endpoints, and what it gets back.

`app.services.identity.grants` says which grant does what.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel

ACCESS_LIFETIME = timedelta(minutes=10)
REFRESH_LIFETIME = timedelta(days=30)

CLIENT_CREDENTIALS = 'client_credentials'
AUTHORIZATION_CODE = 'authorization_code'
REFRESH_TOKEN = 'refresh_token'
TOKEN_EXCHANGE = 'urn:ietf:params:oauth:grant-type:token-exchange'
GRANT_TYPES = [CLIENT_CREDENTIALS, AUTHORIZATION_CODE, REFRESH_TOKEN, TOKEN_EXCHANGE]

ACCESS_TOKEN_TYPE = 'urn:ietf:params:oauth:token-type:access_token'
JWT_TOKEN_TYPE = 'urn:ietf:params:oauth:token-type:jwt'


class TokenRequest(BaseModel):
    """The form a client posts. Which fields matter depends on `grant_type`."""

    grant_type: str
    client_assertion_type: str | None = None
    client_assertion: str | None = None
    organisation: str | None = None
    code: str | None = None
    redirect_uri: str | None = None
    code_verifier: str | None = None
    refresh_token: str | None = None
    subject_token: str | None = None
    subject_token_type: str | None = None
    audience: str | None = None
    #: `urn:igni:params:oauth:grant-type:vm-credential`: the VM's own
    #: credential (`app.services.cloud.vm_credentials`).
    vm_credential: str | None = None
    #: `urn:igni:params:oauth:grant-type:api-key`: a person's API key
    #: (`app.services.identity.api_keys`).
    api_key: str | None = None
    #: `revocations`: a service token for no organisation, good only for
    #: following revocations and the organisations it serves (§3, §21).
    scope: str | None = None
    #: Token exchange: the space an app acts as, with its own token as
    #: `subject_token` (`docs/service-identity.md` §17.2).
    space: str | None = None


class RevocationRequest(BaseModel):
    """The form a client posts to revoke a token (RFC 7009 §2.1)."""

    token: str = ''
    #: `refresh_token` or `access_token`. Only a hint, and not needed:
    #: every igni token says what it is, so the token decides.
    token_type_hint: str | None = None
    client_assertion_type: str | None = None
    client_assertion: str | None = None


class IssuedTokens(BaseModel):
    access_token: str
    token_type: str = 'Bearer'
    expires_in: int
    refresh_token: str | None = None
    issued_token_type: str | None = None
    #: A VM's next credential, from the two VM grants; the presented one
    #: no longer works.
    vm_credential: str | None = None

    @classmethod
    def access(
        cls, access_token: str, *, refresh_token: str | None = None, issued_token_type: str | None = None
    ) -> IssuedTokens:
        """An access token living `ACCESS_LIFETIME`, with what else the grant hands out."""
        return cls(
            access_token=access_token,
            expires_in=int(ACCESS_LIFETIME.total_seconds()),
            refresh_token=refresh_token,
            issued_token_type=issued_token_type,
        )
