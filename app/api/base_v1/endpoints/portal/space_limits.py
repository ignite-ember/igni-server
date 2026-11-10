"""The limits on an app's spaces: the platform's caps, and an organisation's own (`docs/service-identity.md` §17.4)."""

from fastapi import APIRouter, Depends, Request

from app.api.base_v1.schema.portal.user import PortalAccount
from app.api.base_v1.schema.spaces import SpaceCapsBody, SpaceCapsResponse, SpaceQuotaBody, SpaceQuotaResponse
from app.middlewares.request_audit import RequestAudit
from app.security.org_authority import OrgAuthority, require_org_authority
from app.security.portal_auth import get_current_account
from app.security.super_admin import require_super_admin
from app.services.cloud.spaces import SpaceLimits

router = APIRouter()


@router.get('/space-caps', response_model=SpaceCapsResponse)
async def get_caps(_account: PortalAccount = Depends(get_current_account)) -> SpaceCapsResponse:
    return await SpaceLimits().caps()


@router.put('/space-caps', response_model=SpaceCapsResponse)
async def set_caps(
    request: Request, body: SpaceCapsBody, admin: PortalAccount = Depends(require_super_admin)
) -> SpaceCapsResponse:
    RequestAudit.of(request).set_target(target_type='platform_space_caps', target_id='1')
    RequestAudit.of(request).add_detail(body)
    return await SpaceLimits().set_caps(body, by_user_id=admin.user_id)


@router.get('/orgs/{org_id}/space-quota', response_model=SpaceQuotaResponse)
async def get_quota(authority: OrgAuthority = Depends(require_org_authority)) -> SpaceQuotaResponse:
    return await SpaceLimits().quota(authority.org_id)


@router.put('/orgs/{org_id}/space-quota', response_model=SpaceQuotaResponse)
async def set_quota(
    request: Request, body: SpaceQuotaBody, authority: OrgAuthority = Depends(require_org_authority)
) -> SpaceQuotaResponse:
    """An org admin's own limits, each at most the platform's cap."""
    RequestAudit.of(request).add_detail(body)
    return await SpaceLimits().set_quota(authority.org_id, body, by_user_id=authority.user_id)
