"""How to sign in to *this* deployment.

The portal used to hardcode its sign-in buttons and never ask. Two
problems, and the second is the one that made this endpoint necessary
rather than tidy.

A deployment that narrows `OAUTH_PROVIDERS` still showed every button, so a
user clicked a provider the server would refuse and got an error page for
a button that should not have existed. And OIDC, which is the only
provider an air-gapped deployment can reach, had no button at all: the
endpoints existed and nothing linked to them, so the feature was
unreachable by design.

## Unauthenticated, deliberately

This has to be readable before anybody is signed in — it is what the
sign-in page renders from. So it says only which methods exist and what
to call the SSO one. Not the issuer URL, not the client id: a corporate
IdP hostname is a small thing to hand an anonymous caller, and there is
no reason to.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import settings
from app.services.auth.oidc import OidcClient
from app.services.auth.providers import usable_providers

router = APIRouter()


class SsoMethod(BaseModel):
    enabled: bool = Field(description='Whether this deployment has an identity provider configured')
    label: str = Field(description='What the button should say, e.g. "Okta" or "Entra ID"')
    start_url: str = Field(description='Where to send the browser to begin an SSO login')


class AuthMethods(BaseModel):
    """Everything the sign-in page needs to render itself."""

    password: bool = Field(description='Email and password, with a one-time code')
    oauth_providers: list[str] = Field(
        description='Social providers that are enabled **and** configured, in the order to show them'
    )
    sso: SsoMethod


@router.get(
    '/auth/methods',
    response_model=AuthMethods,
    description='Which sign-in methods this deployment offers. Unauthenticated: the login page reads it.',
)
async def auth_methods() -> AuthMethods:
    client = OidcClient()

    return AuthMethods(
        # Always available. There is no setting that turns local accounts
        # off, and inventing one here would be a claim the server does
        # not enforce.
        password=True,
        # Enabled *and* configured. Listing a provider with no
        # credentials renders a button that redirects to the provider
        # with an empty client id — see app/services/auth/providers.py.
        oauth_providers=await usable_providers(),
        sso=SsoMethod(
            enabled=client.is_configured,
            label=settings.oidc_display_name,
            start_url='/v1/auth/oidc',
        ),
    )
