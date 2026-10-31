"""`POST /v1/clients`: a service or app registering itself (RFC 7591).

Unauthenticated, because this is how a client comes to exist. It grants
nothing: the registration waits for a super admin, who compares the
thumbprint returned here with the one the client logged.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.services.identity.registry import ClientRegistration, ClientRegistry, RegistrationRefused, RegistryFull

router = APIRouter()


class RegistrationAnswer(BaseModel):
    """RFC 7591 §3.2.1, plus where the decision stands."""

    client_id: str
    client_id_issued_at: int
    client_name: str
    client_uri: str
    redirect_uris: list[str]
    token_endpoint_auth_method: str = 'private_key_jwt'
    kind: str
    state: str
    thumbprint: str


@router.post('/clients', response_model=RegistrationAnswer, status_code=status.HTTP_201_CREATED, tags=['Identity'])
async def register(body: ClientRegistration) -> RegistrationAnswer:
    try:
        client = await ClientRegistry().register(body)
    except RegistrationRefused as exc:
        # RFC 7591 §3.2.2's error shape.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={'error': 'invalid_client_metadata', 'error_description': str(exc)},
        ) from exc
    except RegistryFull as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    return RegistrationAnswer(
        client_id=client.client_id,
        client_id_issued_at=int(client.created_at.timestamp()),
        client_name=client.name,
        client_uri=client.base_url,
        redirect_uris=list(client.redirect_uris or []),
        kind=client.kind,
        state=client.state,
        thumbprint=client.thumbprint,
    )
