"""VM profiles on the portal, the platform's caps, and an organisation's quota.

Profiles name their organisation in the path: an org admin works in
theirs, a super admin in any (`require_org_authority`). The caps are the
super admin's alone. See `docs/service-identity.md` §12.
"""

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.base_v1.schema.portal.user import PortalAccount
from app.api.base_v1.schema.vm_profiles import (
    AssignVmProfile,
    OrgVmQuotaBody,
    OrgVmQuotaResponse,
    PlatformVmCapsBody,
    PlatformVmCapsResponse,
    VmProfileResponse,
    VmProfileSettings,
    VmProfilesOverview,
)
from app.middlewares.audit import set_audit_detail, set_audit_target
from app.security.org_authority import OrgAuthority, require_org_authority
from app.security.portal_auth import get_current_account
from app.security.super_admin import require_super_admin
from app.services.cloud.vm_profiles import VmCaps, VmProfiles, VmQuotas

router = APIRouter()


@router.get('/vm-caps', response_model=PlatformVmCapsResponse)
async def get_caps(_account: PortalAccount = Depends(get_current_account)) -> PlatformVmCapsResponse:
    """The platform's caps on every profile. Anybody signed in may read them."""
    return await VmCaps().get()


@router.put('/vm-caps', response_model=PlatformVmCapsResponse)
async def set_caps(
    request: Request, body: PlatformVmCapsBody, admin: PortalAccount = Depends(require_super_admin)
) -> PlatformVmCapsResponse:
    set_audit_target(request, target_type='platform_vm_caps', target_id='1')
    set_audit_detail(request, body.model_dump())
    return await VmCaps().set(body, by_user_id=admin.user_id)


@router.get('/orgs/{org_id}/vm-profiles', response_model=VmProfilesOverview)
async def overview(authority: OrgAuthority = Depends(require_org_authority)) -> VmProfilesOverview:
    """The organisation's profiles, the caps and quota, and which group and app has which."""
    return await VmProfiles().overview(authority.org_id)


@router.post('/orgs/{org_id}/vm-profiles', response_model=VmProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_profile(
    request: Request, body: VmProfileSettings, authority: OrgAuthority = Depends(require_org_authority)
) -> VmProfileResponse:
    profile = await VmProfiles().create(authority.org_id, body, by_user_id=authority.user_id)
    set_audit_target(request, target_type='vm_profile', target_id=profile.id)
    set_audit_detail(request, {'name': profile.name, 'version': profile.version})
    return profile


@router.put('/orgs/{org_id}/vm-profiles/{profile_id}', response_model=VmProfileResponse)
async def replace_profile(
    request: Request,
    profile_id: str,
    body: VmProfileSettings,
    authority: OrgAuthority = Depends(require_org_authority),
) -> VmProfileResponse:
    profile = await VmProfiles().replace(authority.org_id, profile_id, body, by_user_id=authority.user_id)
    set_audit_target(request, target_type='vm_profile', target_id=profile_id)
    set_audit_detail(request, {'name': profile.name, 'version': profile.version})
    return profile


@router.delete('/orgs/{org_id}/vm-profiles/{profile_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    request: Request, profile_id: str, authority: OrgAuthority = Depends(require_org_authority)
) -> Response:
    await VmProfiles().delete(authority.org_id, profile_id)
    set_audit_target(request, target_type='vm_profile', target_id=profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put('/orgs/{org_id}/groups/{group_id}/vm-profile', status_code=status.HTTP_204_NO_CONTENT)
async def assign_group(
    request: Request, group_id: str, body: AssignVmProfile, authority: OrgAuthority = Depends(require_org_authority)
) -> Response:
    await VmProfiles().assign_group(authority.org_id, group_id, body.profile_id)
    set_audit_detail(request, {'vm_profile_id': body.profile_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put('/orgs/{org_id}/apps/{app_id}/vm-profile', status_code=status.HTTP_204_NO_CONTENT)
async def assign_app(
    request: Request, app_id: str, body: AssignVmProfile, authority: OrgAuthority = Depends(require_org_authority)
) -> Response:
    await VmProfiles().assign_app(authority.org_id, app_id, body.profile_id)
    set_audit_target(request, target_type='app', target_id=app_id)
    set_audit_detail(request, {'vm_profile_id': body.profile_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/orgs/{org_id}/vm-quota', response_model=OrgVmQuotaResponse)
async def get_quota(authority: OrgAuthority = Depends(require_org_authority)) -> OrgVmQuotaResponse:
    return await VmQuotas().get(authority.org_id)


@router.put('/orgs/{org_id}/vm-quota', response_model=OrgVmQuotaResponse)
async def set_quota(
    request: Request, org_id: str, body: OrgVmQuotaBody, admin: PortalAccount = Depends(require_super_admin)
) -> OrgVmQuotaResponse:
    """The super admin's: an organisation does not set its own quota."""
    authority = await require_org_authority(org_id, admin)
    set_audit_detail(request, body.model_dump())
    return await VmQuotas().set(authority.org_id, body, by_user_id=admin.user_id)
