"""Removed members' retention and legal holds on the portal (`docs/service-identity.md` §18).

The platform's maximum is the super admin's; an organisation's retention
and its holds are its admins' (and the super admin's), in the path.
Every call here is a portal mutation, so the audit trail has it.
"""

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.base_v1.schema.audit_detail import LegalHoldAudit, MemberVmDiscardedAudit
from app.api.base_v1.schema.portal.user import PortalAccount
from app.api.base_v1.schema.retention import (
    AppLegalHoldResponse,
    DiscardedMember,
    DiscardRequest,
    ErasureList,
    LegalHoldBody,
    LegalHoldList,
    LegalHoldResponse,
    OrgRetentionBody,
    OrgRetentionResponse,
    RetentionCapsBody,
    RetentionCapsResponse,
)
from app.middlewares.request_audit import RequestAudit
from app.security.org_authority import OrgAuthority, require_org_authority
from app.security.portal_auth import get_current_account
from app.security.super_admin import require_super_admin
from app.services.cloud.erasure_refusals import ErasureRefusals
from app.services.cloud.erasures import Erasures
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
    RequestAudit.of(request).set_target(target_type='platform_retention', target_id='1')
    RequestAudit.of(request).add_detail(body)
    return await Retention().set_caps(body, by_user_id=admin.user_id)


@router.get('/orgs/{org_id}/retention', response_model=OrgRetentionResponse)
async def get_retention(authority: OrgAuthority = Depends(require_org_authority)) -> OrgRetentionResponse:
    return await Retention().of_org(authority.org_id)


@router.put('/orgs/{org_id}/retention', response_model=OrgRetentionResponse)
async def set_retention(
    request: Request, body: OrgRetentionBody, authority: OrgAuthority = Depends(require_org_authority)
) -> OrgRetentionResponse:
    """For members removed from now on; an erasure already scheduled keeps its time."""
    RequestAudit.of(request).add_detail(body)
    return await Retention().set_org(authority.org_id, body, by_user_id=authority.user_id)


@router.get('/orgs/{org_id}/legal-holds', response_model=LegalHoldList)
async def list_holds(authority: OrgAuthority = Depends(require_org_authority)) -> LegalHoldList:
    return await LegalHolds().list(authority.org_id)


@router.put('/orgs/{org_id}/legal-holds/apps/{app_id}', response_model=AppLegalHoldResponse)
async def place_app_hold(
    request: Request, app_id: str, body: LegalHoldBody, authority: OrgAuthority = Depends(require_org_authority)
) -> AppLegalHoldResponse:
    """The app's VM and its spaces' are not erased while it stands — after inactivity or once discarded."""
    RequestAudit.of(request).set_target(target_type='app_legal_hold', target_id=app_id)
    RequestAudit.of(request).add_detail(LegalHoldAudit(reason=body.reason))
    return await LegalHolds().place_on_app(authority, app_id, body.reason)


@router.delete('/orgs/{org_id}/legal-holds/apps/{app_id}', status_code=status.HTTP_204_NO_CONTENT)
async def release_app_hold(
    request: Request, app_id: str, authority: OrgAuthority = Depends(require_org_authority)
) -> Response:
    RequestAudit.of(request).set_target(target_type='app_legal_hold', target_id=app_id)
    await LegalHolds().release_app(authority, app_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/orgs/{org_id}/erasures', response_model=ErasureList)
async def list_erasures(authority: OrgAuthority = Depends(require_org_authority)) -> ErasureList:
    """Every removed or discarded producer's VM the feed still keeps, and where its erasure stands (§18.5)."""
    return await Erasures().list(authority.org_id)


@router.post('/orgs/{org_id}/members/{user_id}/discard', response_model=DiscardedMember)
async def discard_member(
    request: Request,
    user_id: str,
    body: DiscardRequest | None = None,
    authority: OrgAuthority = Depends(require_org_authority),
) -> DiscardedMember:
    """A removed member's VM erased now, instead of at the end of the retention. Never a current member's."""
    incarnation = body.incarnation if body else None
    RequestAudit.of(request).set_target(target_type='person_vm', target_id=user_id)
    RequestAudit.of(request).add_detail(MemberVmDiscardedAudit(incarnation=incarnation))
    with ErasureRefusals():
        return await Erasures().discard_member(authority, user_id=user_id, incarnation=incarnation)


@router.put('/orgs/{org_id}/legal-holds/{user_id}', response_model=LegalHoldResponse)
async def place_hold(
    request: Request, user_id: str, body: LegalHoldBody, authority: OrgAuthority = Depends(require_org_authority)
) -> LegalHoldResponse:
    RequestAudit.of(request).set_target(target_type='legal_hold', target_id=user_id)
    RequestAudit.of(request).add_detail(LegalHoldAudit(reason=body.reason))
    return await LegalHolds().place(authority, user_id, body.reason)


@router.delete('/orgs/{org_id}/legal-holds/{user_id}', status_code=status.HTTP_204_NO_CONTENT)
async def release_hold(
    request: Request, user_id: str, authority: OrgAuthority = Depends(require_org_authority)
) -> Response:
    RequestAudit.of(request).set_target(target_type='legal_hold', target_id=user_id)
    await LegalHolds().release(authority, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
