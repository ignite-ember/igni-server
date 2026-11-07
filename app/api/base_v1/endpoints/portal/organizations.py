"""Portal organization endpoints — create, list, update and delete orgs, and assign their domains.

The rules live in `app.services.portal.organisations_service`; this module
only maps its refusals to HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.base_v1.schema.portal.organizations import (
    CreateOrgRequest,
    CreateOrgResponse,
    DeleteOrgResponse,
    DeploymentOrg,
    DomainRequest,
    OrgDomains,
    UpdateOrgRequest,
)
from app.api.base_v1.schema.portal.user import OrgSummary, PortalAccount
from app.db.sql.schemas.org_domain import DomainInUse, DomainNotHeld, DomainTaken
from app.security.portal_auth import get_current_account
from app.security.super_admin import require_super_admin
from app.services.portal.org_errors import (
    InvalidDomain,
    InvalidSlug,
    NotOrgAdmin,
    OrgNotFound,
    OrgStillHasMembers,
    PublicMailDomain,
)
from app.services.portal.organisations_service import OrganisationsService

router = APIRouter()


def organisations_service() -> OrganisationsService:
    """One service per request.

    A provider rather than `Depends(OrganisationsService)`: FastAPI would
    read the constructor's keyword arguments as query parameters.
    """
    return OrganisationsService()


def _invalid_slug(exc: InvalidSlug) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


def _not_admin(exc: NotOrgAdmin) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


def _bad_domain(exc: InvalidDomain | PublicMailDomain) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


def _conflict(exc: DomainTaken | DomainInUse) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get(
    '/organizations',
    response_model=list[OrgSummary],
    status_code=status.HTTP_200_OK,
)
async def list_organizations(
    current_user: PortalAccount = Depends(get_current_account),
    service: OrganisationsService = Depends(organisations_service),
) -> list[OrgSummary]:
    """List all organizations the current user belongs to."""
    return await service.list_for(current_user)


@router.post(
    '/organizations',
    response_model=CreateOrgResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    body: CreateOrgRequest,
    current_user: PortalAccount = Depends(require_super_admin),
    service: OrganisationsService = Depends(organisations_service),
) -> CreateOrgResponse:
    """Create a new organization. Super admins only; the creator becomes its admin.

    An install holds one or two organisations, and a person belongs to
    one because they were invited to it. Letting anybody create one
    made every account a potential organisation of its own, which is
    what this replaced.
    """
    try:
        return await service.create(
            display_name=body.display_name, slug=body.slug, domains=body.domains, creator=current_user
        )
    except InvalidSlug as exc:
        raise _invalid_slug(exc) from exc
    except (InvalidDomain, PublicMailDomain) as exc:
        raise _bad_domain(exc) from exc
    except DomainTaken as exc:
        raise _conflict(exc) from exc


@router.get('/deployment/organizations', response_model=list[DeploymentOrg])
async def list_deployment_organizations(
    _account: PortalAccount = Depends(require_super_admin),
    service: OrganisationsService = Depends(organisations_service),
) -> list[DeploymentOrg]:
    """Every organisation on the install, with the domains it owns. Super admins only.

    Names and domains, not contents: a super admin owns the install, not
    the organisations' work.
    """
    return await service.deployment_orgs()


@router.post('/organizations/{org_id}/domains', response_model=OrgDomains)
async def add_domain(
    org_id: str,
    body: DomainRequest,
    _account: PortalAccount = Depends(require_super_admin),
    service: OrganisationsService = Depends(organisations_service),
) -> OrgDomains:
    """Give an organisation another email domain. Super admins only; 409 if another owns it."""
    try:
        return await service.add_domain(org_id, body.domain)
    except OrgNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidDomain, PublicMailDomain) as exc:
        raise _bad_domain(exc) from exc
    except DomainTaken as exc:
        raise _conflict(exc) from exc


@router.delete('/organizations/{org_id}/domains/{domain}', response_model=OrgDomains)
async def remove_domain(
    org_id: str,
    domain: str,
    _account: PortalAccount = Depends(require_super_admin),
    service: OrganisationsService = Depends(organisations_service),
) -> OrgDomains:
    """Release a domain. Super admins only; 404 if not theirs, 409 while any member's address is on it."""
    try:
        return await service.remove_domain(org_id, domain)
    except (InvalidDomain, PublicMailDomain) as exc:
        raise _bad_domain(exc) from exc
    except DomainNotHeld as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainInUse as exc:
        raise _conflict(exc) from exc


@router.patch(
    '/organizations/{org_id}',
    response_model=CreateOrgResponse,
    status_code=status.HTTP_200_OK,
)
async def update_organization(
    org_id: str,
    body: UpdateOrgRequest,
    current_user: PortalAccount = Depends(get_current_account),
    service: OrganisationsService = Depends(organisations_service),
) -> CreateOrgResponse:
    """Update org display name or slug. Must be admin of that org."""
    try:
        return await service.update(org_id, display_name=body.display_name, slug=body.slug, by=current_user)
    except NotOrgAdmin as exc:
        raise _not_admin(exc) from exc
    except InvalidSlug as exc:
        raise _invalid_slug(exc) from exc


@router.delete(
    '/organizations/{org_id}',
    response_model=DeleteOrgResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_organization(
    org_id: str,
    current_user: PortalAccount = Depends(get_current_account),
    service: OrganisationsService = Depends(organisations_service),
) -> DeleteOrgResponse:
    """Delete an organization. Must be admin, and nobody else may still be in it.

    It used to refuse your *last* organisation, because every account
    had one of its own and a person with none was a state nothing
    handled. Having none is normal now: it is where every account
    starts.
    """
    try:
        await service.delete(org_id, by=current_user)
    except NotOrgAdmin as exc:
        raise _not_admin(exc) from exc
    except OrgStillHasMembers as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return DeleteOrgResponse(message='Organization deleted')
