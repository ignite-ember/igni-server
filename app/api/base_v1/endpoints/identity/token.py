"""`POST /v1/oauth/token` and `POST /v1/oauth/revoke`. See `app.services.identity.grants`."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from app.services.cloud.vm_credentials import VM_GRANTS, VmCredentials
from app.services.identity.errors import OAuthError
from app.services.identity.grants import RevocationRequest, TokenGrants, TokenRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post('/oauth/token', tags=['Identity'], description='Issue a token (RFC 6749 §3.2), form-encoded.')
async def token(request: Request) -> JSONResponse:
    form = await request.form()
    try:
        body = TokenRequest.model_validate({name: str(value) for name, value in form.items()})
        # A VM's own credential, and the sign-in that issues a person's:
        # docs/service-identity.md §16.
        issued = await (VmCredentials().grant(body) if body.grant_type in VM_GRANTS else TokenGrants().grant(body))
    except ValidationError:
        return OAuthError.invalid_request('grant_type is required').response()
    except OAuthError as refusal:
        logger.info('Token request refused: %s (%s)', refusal.error, refusal.description)
        return refusal.response()
    return JSONResponse(
        content=issued.model_dump(exclude_none=True),
        headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'},
    )


@router.post(
    '/oauth/revoke',
    tags=['Identity'],
    description='Revoke a refresh token, ending the sign-in it belongs to (RFC 7009), form-encoded.',
)
async def revoke(request: Request) -> Response:
    form = await request.form()
    try:
        body = RevocationRequest.model_validate({name: str(value) for name, value in form.items()})
        await TokenGrants().revoke(body)
    except ValidationError:
        return OAuthError.invalid_request('token is required').response()
    except OAuthError as refusal:
        logger.info('Revocation refused: %s (%s)', refusal.error, refusal.description)
        return refusal.response()
    # RFC 7009 §2.2: 200 whether or not there was anything to revoke.
    return Response(status_code=200, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})
