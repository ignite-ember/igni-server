"""Database layer for organization management."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.org_membership import ACTIVE, OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.models.tenant import ACTIVE as TENANT_ACTIVE, Tenant
from app.db.sql.org_domains import OrgDomainDB
from app.db.sql.organisation_deletion_notices import OrganisationDeletionNoticeDB
from app.db.sql.schemas.organization import SlugTaken
from app.security.account_domains import email_domain
from app.utils.singleton import SingletonMeta

#: The unique index a duplicate slug violates.
_SLUG_INDEX = 'organizations_slug_key'

#: Membership states that hold a person's one organisation (migration 0022).
_HOLDS_A_PLACE = ('active', 'deactivated')


class OrganizationDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """Database operations for organizations."""

    def __init__(
        self,
        *,
        domains: OrgDomainDB | None = None,
        deletion_notices: OrganisationDeletionNoticeDB | None = None,
    ) -> None:
        self._domains = domains or OrgDomainDB()
        self._deletion_notices = deletion_notices or OrganisationDeletionNoticeDB()

    async def get_by_id(self, org_id: str) -> Organization | None:
        async with self.session_scope() as session:
            result = await session.execute(select(Organization).where(Organization.id == org_id))
            return result.scalars().first()

    async def get_by_slug(self, slug: str) -> Organization | None:
        async with self.session_scope() as session:
            result = await session.execute(select(Organization).where(Organization.slug == slug))
            return result.scalars().first()

    async def list_user_orgs(self, user_id: str) -> list[tuple[Organization, OrgMembership]]:
        """Return all orgs a user belongs to, with their membership."""
        async with self.session_scope() as session:
            result = await session.execute(
                select(Organization, OrgMembership)
                .join(OrgMembership, OrgMembership.org_id == Organization.id)
                .where(OrgMembership.user_id == user_id, OrgMembership.status == ACTIVE)
                .order_by(Organization.created_at)
            )
            return [(row[0], row[1]) for row in result.all()]

    async def member_org_ids(self, user_id: str, *, active_only: bool = True) -> list[str]:
        """The organisations a person is a member of.

        For data that starts from a person rather than an organisation —
        their notifications, their group. In practice one: a company
        address belongs to one organisation's domain.
        """
        query = select(OrgMembership.org_id).where(OrgMembership.user_id == user_id)
        if active_only:
            query = query.where(OrgMembership.status == ACTIVE)
        async with self.session_scope() as session:
            rows = await session.execute(query.order_by(OrgMembership.org_id))
            return [str(org_id) for org_id in rows.scalars().all()]

    async def provisioned_member_org_ids(self, user_id: str) -> list[str]:
        """The organisations a person is an active member of that have a database.

        For *reading* what starts from a person — their notifications —
        across every organisation they belong to. An organisation whose
        database was never provisioned has nothing written to it yet, so
        it is not provisioned just to be read; a detached one is not
        routed to at all. A membership that has ended drops its
        organisation here, and with it everything that lives there.
        """
        query = (
            select(OrgMembership.org_id)
            .join(Tenant, Tenant.org_id == OrgMembership.org_id)
            .where(OrgMembership.user_id == user_id, OrgMembership.status == ACTIVE, Tenant.state == TENANT_ACTIVE)
            .order_by(OrgMembership.org_id)
        )
        async with self.session_scope() as session:
            rows = await session.execute(query)
            return [str(org_id) for org_id in rows.scalars().all()]

    async def all_with_member_counts(self) -> list[tuple[Organization, int]]:
        """Every organisation and how many active members it has, oldest first."""
        async with self.session_scope() as session:
            members = (
                select(OrgMembership.org_id, func.count().label('members'))
                .where(OrgMembership.status == ACTIVE)
                .group_by(OrgMembership.org_id)
                .subquery()
            )
            result = await session.execute(
                select(Organization, func.coalesce(members.c.members, 0))
                .outerjoin(members, members.c.org_id == Organization.id)
                .order_by(Organization.created_at)
            )
            return [(row[0], int(row[1])) for row in result.all()]

    async def create(
        self,
        *,
        display_name: str,
        slug: str,
        owner_user_id: str,
        domains: list[str],
        owner_email: str | None = None,
    ) -> Organization:
        """Create an organisation owning ``domains``. `SlugTaken`, `DomainTaken`.

        The creator becomes its admin only when their own address is on
        one of its domains: a person belongs to the organisation owning
        their domain and no other. Otherwise the first person to join
        administers it (`Admission.admit`) — the case of a super admin
        creating a second organisation, or the bootstrap account
        creating the first under an address it will move off.
        """
        async with self.session_scope() as session:
            now = datetime.now(timezone.utc)
            org = Organization(
                id=str(uuid.uuid4()),
                slug=slug,
                display_name=display_name,
                owner_user_id=owner_user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(org)
            # Before seating the founder: its query would autoflush the org and
            # a taken slug would escape as a raw IntegrityError.
            await self._flush_refusing_taken_slug(session, slug=slug)
            if email_domain(owner_email) in set(domains):
                await self._seat_founder(session, org_id=org.id, user_id=owner_user_id, now=now)
            await self._domains.claim(session, org_id=org.id, domains=domains)
            return org

    async def ensure_exists(self, *, org_id: str, slug: str, display_name: str, owner_user_id: str) -> None:
        """Create the org and owner membership if they don't already exist."""
        async with self.session_scope() as session:
            if await session.get(Organization, org_id) is not None:
                return
            now = datetime.now(timezone.utc)
            session.add(
                Organization(
                    id=org_id,
                    slug=slug,
                    display_name=display_name,
                    owner_user_id=owner_user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            await self._seat_founder(session, org_id=org_id, user_id=owner_user_id, now=now)
            await session.flush()

    async def delete(self, org_id: str) -> None:
        """Delete an organization and all its memberships.

        Every approved service is owed the news, in the same transaction
        (`OrganisationDeletionNoticeDB.owe`); it is delivered afterwards.
        Its own database is the caller's to detach (`TenantRegistryDB.detach`).
        """
        async with self.session_scope() as session:
            await session.execute(OrgMembership.__table__.delete().where(OrgMembership.org_id == org_id))
            await session.execute(Organization.__table__.delete().where(Organization.id == org_id))
            await self._deletion_notices.owe(session, [org_id], now=datetime.now(timezone.utc))
            await session.flush()

    async def update(
        self, org_id: str, *, display_name: str | None = None, slug: str | None = None
    ) -> Organization | None:
        """Rename the organisation or change its slug; ``None`` if there is no such organisation. `SlugTaken`."""
        async with self.session_scope() as session:
            org = await session.get(Organization, org_id)
            if org is None:
                return None
            if display_name is not None:
                org.display_name = display_name
            if slug is not None:
                org.slug = slug
            org.updated_at = datetime.now(timezone.utc)
            await self._flush_refusing_taken_slug(session, slug=org.slug)
            return org

    async def _seat_founder(self, session: AsyncSession, *, org_id: str, user_id: str, now: datetime) -> None:
        """Make *user_id* the new organisation's admin, unless they already hold a place in one.

        A person is in one organisation at a time (migration 0022), so somebody
        already in another — the dev account, an admin creating a second — is not added.
        """
        held = await session.execute(
            select(OrgMembership.id).where(OrgMembership.user_id == user_id, OrgMembership.status.in_(_HOLDS_A_PLACE))
        )
        if held.first() is not None:
            return
        session.add(
            OrgMembership(
                id=str(uuid.uuid4()),
                org_id=org_id,
                user_id=user_id,
                role='admin',
                status=ACTIVE,
                created_at=now,
                updated_at=now,
            )
        )

    async def _flush_refusing_taken_slug(self, session: AsyncSession, *, slug: str) -> None:
        """Flush; a duplicate slug is `SlugTaken`, any other violation propagates.

        Matched on the index name: every other key here is a fresh uuid, and a
        constraint added later must surface as itself, not as a taken slug.
        """
        try:
            await session.flush()
        except IntegrityError as exc:
            if _SLUG_INDEX not in str(getattr(exc, 'orig', exc)):
                raise
            raise SlugTaken(slug) from exc
