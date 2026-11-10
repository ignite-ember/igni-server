"""What the token endpoint hands out, and to whom.

Four grants, and every token they produce names one organisation:

* **client_credentials** — a client acting as itself, for igni
  (`aud` = the issuer). A service names one organisation
  (`organisation` is required) and gets a `service` token to call the
  directory with; an app gets an `app` token for the one organisation
  it belongs to (`kind: app`, no `group`); a platform client (the
  cloud's gateway and node agent) gets a `platform` token naming no
  organisation.
* **authorization_code** (+ PKCE) — a person, signed in to a service's
  own portal by way of igni's. The token is for that client's URL only.
* **refresh_token** — renews the above. Refused once the person has
  been deactivated, deleted, removed from the organisation, or has had
  their sessions withdrawn — which is how a revocation reaches a service
  that never calls back — and once the client itself has revoked the
  sign-in (`revoke`, RFC 7009), which is how a person signing out of a
  service ends it at igni too.
* **token-exchange** (RFC 8693) — the CLI trading its igni session for a
  token a service will accept. No client authentication and no
  registration needed: the session is the credential, and the result is
  only ever for the named audience — the service's URL.

Each grant is a class of its own (`client_credentials_grant`,
`sign_in_grants`, `token_exchange`); `TokenGrants` picks one by
`grant_type`. Revoking a sign-in is `sign_in_revocation`.

Access tokens live `ACCESS_LIFETIME`. That is the whole of revocation
for a service: it does not ask igni whether a person is still allowed on
every request, it holds a token that says so for ten minutes.
"""

from __future__ import annotations

from typing import Protocol

from app.security.subject import Subjects
from app.services.auth.jwt_service import JWTService
from app.services.identity.client_credentials_grant import ClientCredentialsGrant
from app.services.identity.errors import OAuthError
from app.services.identity.people_in_organisations import PeopleInOrganisations
from app.services.identity.person_tokens import PersonTokens
from app.services.identity.sign_in_grants import AuthorizationCodeGrant, RefreshGrant
from app.services.identity.token_clients import TokenClients
from app.services.identity.token_exchange import TokenExchangeGrant
from app.services.identity.token_requests import GRANT_TYPES, IssuedTokens, TokenRequest


class TokenGrant(Protocol):
    grant_type: str

    async def grant(self, request: TokenRequest) -> IssuedTokens: ...


class TokenGrants:
    """The grant a token request names, of the four above."""

    def __init__(self, *, grants: list[TokenGrant] | None = None) -> None:
        self._by_type = {grant.grant_type: grant for grant in grants or self.standard()}

    @staticmethod
    def standard() -> list[TokenGrant]:
        """The four grants, sharing one signer and one client authentication."""
        tokens = JWTService()
        clients = TokenClients(tokens=tokens)
        people = PeopleInOrganisations(subjects=Subjects(tokens=tokens))
        person_tokens = PersonTokens(tokens)
        return [
            ClientCredentialsGrant(clients=clients, tokens=tokens),
            AuthorizationCodeGrant(clients=clients, people=people, person_tokens=person_tokens),
            RefreshGrant(clients=clients, tokens=tokens, people=people, person_tokens=person_tokens),
            TokenExchangeGrant(tokens=tokens, people=people, person_tokens=person_tokens),
        ]

    async def grant(self, request: TokenRequest) -> IssuedTokens:
        grant = self._by_type.get(request.grant_type)
        if grant is None:
            raise OAuthError(
                'unsupported_grant_type',
                f'grant_type must be one of {", ".join(GRANT_TYPES)}, or a VM grant (docs/service-identity.md §16)',
            )
        return await grant.grant(request)
