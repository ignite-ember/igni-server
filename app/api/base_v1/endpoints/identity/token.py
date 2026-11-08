"""`POST /v1/oauth/token` and `POST /v1/oauth/revoke`. See `app.services.identity.grants`."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from app.api.base_v1.schema.audit_detail import TokenGrantAudit
from app.middlewares.request_audit import RequestAudit
from app.services.cloud.vm_credential_grants import VM_GRANTS, VmCredentialGrants
from app.services.identity.api_keys import API_KEY, ApiKeys, ApiKeyString
from app.services.identity.errors import OAuthError
from app.services.identity.grants import IssuedTokens, RevocationRequest, TokenGrants, TokenRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post('/oauth/token', tags=['Identity'], description='Issue a token (RFC 6749 §3.2), form-encoded.')
async def token(request: Request) -> JSONResponse:
    form = await request.form()
    try:
        body = TokenRequest.model_validate({name: str(value) for name, value in form.items()})
        if body.grant_type == API_KEY:
            issued = await _api_key(request, body)
        else:
            # A VM's own credential, and the sign-in that issues a person's:
            # docs/service-identity.md §16.
            issued = await (
                VmCredentialGrants().grant(body) if body.grant_type in VM_GRANTS else TokenGrants().grant(body)
            )
    except ValidationError:
        return OAuthError.invalid_request('grant_type is required').response()
    except OAuthError as refusal:
        logger.info('Token request refused: %s (%s)', refusal.error, refusal.description)
        return refusal.response()
    return JSONResponse(
        content=issued.model_dump(exclude_none=True),
        headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'},
    )


async def _api_key(request: Request, body: TokenRequest) -> IssuedTokens:
    """A personal API key exchanged for a session (§22) — recorded, granted or refused."""
    RequestAudit.of(request).record_regardless()
    RequestAudit.of(request).add_detail(TokenGrantAudit())
    try:
        RequestAudit.of(request).set_target(
            target_type='api_key', target_id=ApiKeyString.parse(body.api_key or '').key_id
        )
    except OAuthError:
        pass  # not even shaped like one: refused below, recorded without a target
    exchanged = await ApiKeys().exchange(body)
    RequestAudit.of(request).attribute_to(user_id=exchanged.user_id, email=None, org_id=exchanged.org_id)
    return exchanged.issued


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
