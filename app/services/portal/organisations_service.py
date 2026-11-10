"""Creating, listing, changing and deleting organisations, and the domains they own.

Who may do what is decided here, not in the router: a super admin creates
organisations and assigns their domains; an organisation's own admin
renames or deletes it. Each refusal is a typed error from `org_errors`, or from the domain store's
`app.db.sql.schemas.org_domain`.
"""

from __future__ import annotations

from app.api.base_v1.schema.portal.organizations import CreateOrgResponse, DeploymentOrg, OrgDomains
from app.api.base_v1.schema.portal.user import OrgSummary, PortalAccount
from app.db.sql.models.organization import Organization
from app.db.sql.org_domains import OrgDomainDB
from app.db.sql.organizations import OrganizationDB
from app.db.sql.team import TeamDB
from app.db.sql.tenant_registry import TenantRegistryDB
from app.services.cloud.producer_feed import ProducerFeed
from app.services.identity.organisation_deletion_wake import DeletionWake, ProcessDeletionWake
from app.services.portal.company_domain import CompanyDomain
from app.services.portal.org_errors import NotOrgAdmin, OrgNotFound, OrgStillHasMembers
from app.services.portal.org_slug import OrgSlug

#: Memberships that still count as being in the organisation.
_STILL_IN = ('active', 'invited')


class OrganisationsService:
    def __init__(
        self,
        *,
        orgs: OrganizationDB | None = None,
        domains: OrgDomainDB | None = None,
        team: TeamDB | None = None,
        tenants: TenantRegistryDB | None = None,
        feed: ProducerFeed | None = None,
        deletion_wake: DeletionWake | None = None,
    ) -> None:
        self._orgs = orgs or OrganizationDB()
        self._domains = domains or OrgDomainDB()
        self._team = team or TeamDB()
        self._tenants = tenants or TenantRegistryDB()
        self._feed = feed or ProducerFeed()
        self._deletion_wake = deletion_wake or ProcessDeletionWake()

    async def list_for(self, account: PortalAccount) -> list[OrgSummary]:
        """Every organisation the account belongs to, with its role in each."""
        return [
            OrgSummary(id=org.id, slug=org.slug, display_name=org.display_name, role=membership.role)
            for org, membership in await self._orgs.list_user_orgs(account.user_id)
        ]

    async def create(
        self, *, display_name: str, slug: str | None, domains: list[str], creator: PortalAccount
    ) -> CreateOrgResponse:
        """A new organisation owning *domains*; the creator becomes its admin.

        `InvalidSlug`, `SlugTaken`, `InvalidDomain`, `PublicMailDomain`, `DomainTaken`.
        """
        org = await self._orgs.create(
            display_name=display_name,
            slug=OrgSlug.for_new_org(slug=slug, display_name=display_name),
            owner_user_id=creator.user_id,
            domains=sorted({CompanyDomain.checked(domain) for domain in domains}),
            owner_email=creator.email,
        )
        return self._response(org)

    async def deployment_orgs(self) -> list[DeploymentOrg]:
        """Every organisation on the install, with its domains and member count."""
        return [
            DeploymentOrg(
                id=str(org.id),
                slug=str(org.slug),
                display_name=str(org.display_name),
                domains=await self._domains.domains_of(str(org.id)),
                members=members,
            )
            for org, members in await self._orgs.all_with_member_counts()
        ]

    async def add_domain(self, org_id: str, domain: str) -> OrgDomains:
        """Give the organisation another domain. `OrgNotFound`, `InvalidDomain`, `PublicMailDomain`, `DomainTaken`."""
        if await self._orgs.get_by_id(org_id) is None:
            raise OrgNotFound()
        return OrgDomains(org_id=org_id, domains=await self._domains.add(org_id, CompanyDomain.checked(domain)))

    async def remove_domain(self, org_id: str, domain: str) -> OrgDomains:
        """Release a domain. `InvalidDomain`, `PublicMailDomain`, `DomainNotHeld`, `DomainInUse`."""
        return OrgDomains(org_id=org_id, domains=await self._domains.remove(org_id, CompanyDomain.checked(domain)))

    async def update(
        self, org_id: str, *, display_name: str | None, slug: str | None, by: PortalAccount
    ) -> CreateOrgResponse:
        """Rename the organisation or change its slug. `NotOrgAdmin`, `InvalidSlug`, `SlugTaken`, `OrgNotFound`."""
        await self._require_admin(org_id, by)
        checked = OrgSlug.checked(slug) if slug else None
        org = await self._orgs.update(org_id, display_name=display_name, slug=checked)
        if org is None:
            raise OrgNotFound()
        return self._response(org)

    async def delete(self, org_id: str, *, by: PortalAccount) -> None:
        """Delete the organisation once nobody but *by* is still in it. `NotOrgAdmin`, `OrgStillHasMembers`.

        Its own database is kept, detached: nothing routes to it again,
        and an operator can still read or drop it.
        """
        await self._require_admin(org_id, by)
        members, _ = await self._team.list_members(org_id)
        others = [m for m in members if m.status in _STILL_IN and m.user_id != by.user_id]
        if others:
            raise OrgStillHasMembers(len(others))
        # Deactivated members are still in it, and go with it: their removal
        # is numbered here, like any other member's, not left for a later read.
        async with self._feed.removal():
            await self._orgs.delete(org_id)
            await self._tenants.detach(org_id)
        # Tell the services now rather than at the next round.
        self._deletion_wake.nudge()

    async def _require_admin(self, org_id: str, account: PortalAccount) -> None:
        """Admin of *org_id*, or `NotOrgAdmin`. One copy, because a duplicated authorization rule drifts."""
        membership = await self._team.get_active_member(org_id, account.user_id)
        if not membership or membership.role != 'admin':
            raise NotOrgAdmin()

    @staticmethod
    def _response(org: Organization) -> CreateOrgResponse:
        return CreateOrgResponse(id=org.id, slug=org.slug, display_name=org.display_name)
