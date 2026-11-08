"""What a service deployed with only this server's URL reads first.

`/.well-known/openid-configuration` names every endpoint, and
`/.well-known/jwks.json` holds the public keys igni's tokens are signed
with. Both are public by nature, and both are served at the root rather
than under `/v1` because that is where RFC 8414 and OIDC Discovery say a
client looks.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.config import settings
from app.services.auth.jwt_service import JWTService
from app.services.identity.assertions import ASSERTION_TYPE
from app.services.identity.key_ring import RELOAD_EVERY, SigningKeys
from app.services.identity.token_clients import TokenClients
from app.services.identity.token_requests import GRANT_TYPES

router = APIRouter()


class ServerMetadata(BaseModel):
    issuer: str
    jwks_uri: str
    token_endpoint: str
    revocation_endpoint: str
    authorization_endpoint: str
    registration_endpoint: str
    directory_endpoint: str
    grant_types_supported: list[str]
    response_types_supported: list[str] = ['code']
    code_challenge_methods_supported: list[str] = ['S256']
    token_endpoint_auth_methods_supported: list[str] = ['private_key_jwt']
    token_endpoint_auth_signing_alg_values_supported: list[str] = ['RS256', 'ES256']
    revocation_endpoint_auth_methods_supported: list[str] = ['private_key_jwt']
    revocation_endpoint_auth_signing_alg_values_supported: list[str] = ['RS256', 'ES256']
    id_token_signing_alg_values_supported: list[str] = ['RS256']
    subject_types_supported: list[str] = ['public']
    client_assertion_type: str = ASSERTION_TYPE
    #: Fleet's gateway, where a producer's igni is reached (`FLEET_GATEWAY_URL`);
    #: absent when this deployment has no Fleet. igni's SDK reads it so a program
    #: needs only this server's address.
    igni_gateway: str | None = None


@router.get(
    '/.well-known/openid-configuration',
    response_model=ServerMetadata,
    response_model_exclude_none=True,
    tags=['Identity'],
)
async def metadata(response: Response) -> ServerMetadata:
    response.headers['Cache-Control'] = f'public, max-age={int(RELOAD_EVERY.total_seconds())}'
    issuer = JWTService().issuer
    clients = TokenClients()
    # A field left out rather than published as null when there is no Fleet.
    return ServerMetadata(
        issuer=issuer,
        jwks_uri=f'{issuer}/.well-known/jwks.json',
        token_endpoint=clients.token_endpoint,
        revocation_endpoint=clients.revocation_endpoint,
        # The portal, not this API: signing in is something a person
        # does in a browser, and the portal is where their session is.
        authorization_endpoint=f'{settings.portal_base_url.rstrip("/")}/authorize',
        registration_endpoint=f'{issuer}/v1/clients',
        directory_endpoint=f'{issuer}/v1/directory',
        grant_types_supported=GRANT_TYPES,
        igni_gateway=settings.fleet_gateway_url.rstrip('/') or None,
    )


@router.get('/.well-known/jwks.json', tags=['Identity'])
async def jwks(response: Response) -> dict[str, list[dict[str, str]]]:
    # Cacheable for as long as a replica keeps its own copy: a staged
    # key is published for twice that before anything signs with it.
    response.headers['Cache-Control'] = f'public, max-age={int(RELOAD_EVERY.total_seconds())}'
    return {'keys': [key.as_dict() for key in SigningKeys.ring().published.keys]}
