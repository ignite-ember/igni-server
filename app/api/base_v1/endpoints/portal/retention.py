"""Removed members' retention and legal holds on the portal (`docs/service-identity.md` §18).

The platform's maximum is the super admin's; an organisation's retention
and its holds are its admins' (and the super admin's), in the path.
Every call here is a portal mutation, so the audit trail has it.
"""

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.base_v1.schema.portal.user import PortalAccount
from app.api.base_v1.schema.retention import (
    LegalHoldBody,
    LegalHoldList,
    LegalHoldResponse,
    OrgRetentionBody,
    OrgRetentionResponse,
    RetentionCapsBody,
    RetentionCapsResponse,
)
from app.middlewares.audit import set_audit_detail, set_audit_target
from app.security.org_authority import OrgAuthority, require_org_authority
from app.security.portal_auth import get_current_account
from app.security.super_admin import require_super_admin
from app.services.cloud.legal_holds import LegalHolds
from app.services.cloud.retention_rules import Retention

router = APIRouter()


@router.get('/retention-caps', response_model=RetentionCapsResponse)
async def get_caps(_account: PortalAccount = Depends(get_current_account)) -> RetentionCapsResponse:
    return await Retention().caps()


@router.put('/retention-caps', response_model=RetentionCapsResponse)
async def set_caps(
    request: Request, body: RetentionCapsBody, admin: PortalAccount = Depends(require_super_admin)
) -> RetentionCapsResponse:
    set_audit_target(request, target_type='platform_retention', target_id='1')
    set_audit_detail(request, body.model_dump())
    return await Retention().set_caps(body, by_user_id=admin.user_id)


@router.get('/orgs/{org_id}/retention', response_model=OrgRetentionResponse)
async def get_retention(authority: OrgAuthority = Depends(require_org_authority)) -> OrgRetentionResponse:
    return await Retention().of_org(authority.org_id)


@router.put('/orgs/{org_id}/retention', response_model=OrgRetentionResponse)
async def set_retention(
    request: Request, body: OrgRetentionBody, authority: OrgAuthority = Depends(require_org_authority)
) -> OrgRetentionResponse:
    """For members removed from now on; an erasure already scheduled keeps its time."""
    set_audit_detail(request, body.model_dump())
    return await Retention().set_org(authority.org_id, body, by_user_id=authority.user_id)


@router.get('/orgs/{org_id}/legal-holds', response_model=LegalHoldList)
async def list_holds(authority: OrgAuthority = Depends(require_org_authority)) -> LegalHoldList:
    return await LegalHolds().list(authority.org_id)


@router.put('/orgs/{org_id}/legal-holds/{user_id}', response_model=LegalHoldResponse)
async def place_hold(
    request: Request, user_id: str, body: LegalHoldBody, authority: OrgAuthority = Depends(require_org_authority)
) -> LegalHoldResponse:
    set_audit_target(request, target_type='legal_hold', target_id=user_id)
    set_audit_detail(request, {'reason': body.reason})
    return await LegalHolds().place(authority, user_id, body.reason)


@router.delete('/orgs/{org_id}/legal-holds/{user_id}', status_code=status.HTTP_204_NO_CONTENT)
async def release_hold(
    request: Request, user_id: str, authority: OrgAuthority = Depends(require_org_authority)
) -> Response:
    set_audit_target(request, target_type='legal_hold', target_id=user_id)
    await LegalHolds().release(authority, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
