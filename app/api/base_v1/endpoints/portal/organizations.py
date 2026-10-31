"""Portal organization endpoints — create, list, update orgs."""

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.base_v1.schema.portal.user import OrgSummary, PortalAccount
from app.db.sql.org_domains import normalise_domain, OrgDomainDB
from app.db.sql.organizations import OrganizationDB
from app.db.sql.team import TeamDB
from app.security.portal_auth import get_current_account
from app.security.super_admin import require_super_admin
from app.services.identity.organisation_deletions import OrganisationDeletions

router = APIRouter()
_db = OrganizationDB()
_domains = OrgDomainDB()
_team_db = TeamDB()


class CreateOrgRequest(BaseModel):
    display_name: str
    slug: str | None = None
    #: The email domains it owns. A person belongs to the organisation
    #: owning their address's domain, so an organisation with none could
    #: only ever have its creator in it.
    domains: list[str] = Field(min_length=1, max_length=20)


class DomainRequest(BaseModel):
    domain: str


class OrgDomains(BaseModel):
    org_id: str
    domains: list[str]


class DeploymentOrg(BaseModel):
    """One organisation as a super admin sees it: what it is and what it owns."""

    id: str
    slug: str
    display_name: str
    domains: list[str]
    members: int


class CreateOrgResponse(BaseModel):
    id: str
    slug: str
    display_name: str


class UpdateOrgRequest(BaseModel):
    display_name: str | None = None
    slug: str | None = None


#: 3-32 characters, lowercase alphanumeric and hyphens, no hyphen at
#: either end. Named once because two paths produce a slug and only one
#: of them used to check it.
_SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$')


def _validate_slug(slug: str) -> str:
    """Check a slug the caller supplied. Rejects, never repairs."""
    slug = slug.lower().strip()
    if not _SLUG.match(slug):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Slug must be 3-32 chars, lowercase alphanumeric and hyphens, no leading/trailing hyphens',
        )
    return slug


def _derive_slug(display_name: str) -> str:
    """Make a slug from a display name. Always returns a valid one.

    The derivation never went through `_validate_slug`, so the
    3-32-character rule held only for slugs somebody typed. What it
    produced instead, for names that are not unreasonable:

        '日本語'  -> ''     every character is non-alphanumeric
        '!!!'    -> ''
        'Ab'     -> 'ab'   two characters, under the minimum

    `organizations.slug` is NOT NULL and UNIQUE, so an empty string
    stored fine — and the *second* org whose name derived to empty
    collided with the first and was told its slug was taken.

    A name that cannot produce a slug gets a generated one rather than
    a 422: refusing "日本語" as an organisation name is the wrong
    answer, and the slug is editable afterwards. `strip('-')` runs
    again after the truncation, because cutting at 32 can land on a
    hyphen and leave one trailing.
    """
    slug = re.sub(r'[^a-z0-9]+', '-', display_name.lower()).strip('-')[:32].strip('-')
    if _SLUG.match(slug):
        return slug
    # 16 characters, so it satisfies the pattern by construction.
    return f'org-{uuid.uuid4().hex[:12]}'


async def _require_org_admin(org_id: str, user: PortalAccount) -> None:
    """Admin of *org_id*, or 403.

    One copy. It was written out twice — fetch membership, test the
    role, raise — and a duplicated authorization rule is one that
    drifts.
    """
    membership = await _team_db.get_active_member(org_id, user.user_id)
    if not membership or membership.role != 'admin':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Admin access required for this organization')


@router.get(
    '/organizations',
    response_model=list[OrgSummary],
    status_code=status.HTTP_200_OK,
)
async def list_organizations(
    current_user: PortalAccount = Depends(get_current_account),
) -> list[OrgSummary]:
    """List all organizations the current user belongs to."""
    user_orgs = await _db.list_user_orgs(current_user.user_id)
    return [
        OrgSummary(
            id=org.id,
            slug=org.slug,
            display_name=org.display_name,
            role=mem.role,
        )
        for org, mem in user_orgs
    ]


@router.post(
    '/organizations',
    response_model=CreateOrgResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    body: CreateOrgRequest,
    current_user: PortalAccount = Depends(require_super_admin),
) -> CreateOrgResponse:
    """Create a new organization. Super admins only; the creator becomes its admin.

    An install holds one or two organisations, and a person belongs to
    one because they were invited to it. Letting anybody create one
    made every account a potential organisation of its own, which is
    what this replaced.
    """
    slug = _validate_slug(body.slug) if body.slug else _derive_slug(body.display_name)

    org = await _db.create(
        display_name=body.display_name,
        slug=slug,
        owner_user_id=current_user.user_id,
        domains=sorted({normalise_domain(domain) for domain in body.domains}),
        owner_email=current_user.email,
    )
    return CreateOrgResponse(id=org.id, slug=org.slug, display_name=org.display_name)


@router.get('/deployment/organizations', response_model=list[DeploymentOrg])
async def list_deployment_organizations(
    _account: PortalAccount = Depends(require_super_admin),
) -> list[DeploymentOrg]:
    """Every organisation on the install, with the domains it owns. Super admins only.

    Names and domains, not contents: a super admin owns the install, not
    the organisations' work.
    """
    return [
        DeploymentOrg(
            id=str(org.id),
            slug=str(org.slug),
            display_name=str(org.display_name),
            domains=await _domains.domains_of(str(org.id)),
            members=members,
        )
        for org, members in await _db.all_with_member_counts()
    ]


@router.post('/organizations/{org_id}/domains', response_model=OrgDomains)
async def add_domain(
    org_id: str,
    body: DomainRequest,
    _account: PortalAccount = Depends(require_super_admin),
) -> OrgDomains:
    """Give an organisation another email domain. Super admins only; 409 if another owns it."""
    if await _db.get_by_id(org_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such organization.')
    return OrgDomains(org_id=org_id, domains=await _domains.add(org_id, normalise_domain(body.domain)))


@router.delete('/organizations/{org_id}/domains/{domain}', response_model=OrgDomains)
async def remove_domain(
    org_id: str,
    domain: str,
    _account: PortalAccount = Depends(require_super_admin),
) -> OrgDomains:
    """Release a domain. Super admins only; 409 while any member's address is on it."""
    return OrgDomains(org_id=org_id, domains=await _domains.remove(org_id, normalise_domain(domain)))


@router.patch(
    '/organizations/{org_id}',
    response_model=CreateOrgResponse,
    status_code=status.HTTP_200_OK,
)
async def update_organization(
    org_id: str,
    body: UpdateOrgRequest,
    current_user: PortalAccount = Depends(get_current_account),
) -> CreateOrgResponse:
    """Update org display name or slug. Must be admin of that org."""
    await _require_org_admin(org_id, current_user)

    slug = _validate_slug(body.slug) if body.slug else None
    org = await _db.update(org_id, display_name=body.display_name, slug=slug)
    return CreateOrgResponse(id=org.id, slug=org.slug, display_name=org.display_name)


class DeleteOrgResponse(BaseModel):
    message: str


@router.delete(
    '/organizations/{org_id}',
    response_model=DeleteOrgResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_organization(
    org_id: str,
    current_user: PortalAccount = Depends(get_current_account),
) -> DeleteOrgResponse:
    """Delete an organization. Must be admin, and nobody else may still be in it.

    It used to refuse your *last* organisation, because every account
    had one of its own and a person with none was a state nothing
    handled. Having none is normal now: it is where every account
    starts.
    """
    await _require_org_admin(org_id, current_user)

    members, _ = await _team_db.list_members(org_id)
    active_others = [m for m in members if m.status in ('active', 'invited') and m.user_id != current_user.user_id]
    if active_others:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Remove all members before deleting. {len(active_others)} member(s) still in this organization.',
        )

    await _db.delete(org_id)
    # Tell the services now rather than at the next round.
    OrganisationDeletions.nudge()
    return DeleteOrgResponse(message='Organization deleted')
