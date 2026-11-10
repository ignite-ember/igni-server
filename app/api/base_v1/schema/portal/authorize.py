"""What the portal's `/authorize` page asks and posts when a person signs in to a registered client."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OrganisationChoice(BaseModel):
    org_id: str
    name: str


class AuthorizationAsk(BaseModel):
    """`GET /oauth/request`: who is asking, and the organisations the person may pick."""

    client_name: str
    organisations: list[OrganisationChoice]


class Authorize(BaseModel):
    """`POST /oauth/authorize`: the person's choice, and the client's PKCE challenge."""

    client_id: str
    redirect_uri: str
    organisation: str
    code_challenge: str = Field(min_length=43, max_length=128)
    code_challenge_method: str = 'S256'
    state: str = Field(default='', max_length=1000)


class Redirect(BaseModel):
    """Where the page sends the person next: the client's redirect URI, carrying the code."""

    redirect_to: str
