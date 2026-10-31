"""Database layer for organization management."""

from datetime import datetime, timezone
import logging
import re
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.org_domains import OrgDomainDB
from app.db.sql.organisation_deletion_notices import OrganisationDeletionNoticeDB
from app.db.sql.tenancy import Tenants
from app.security.account_domains import email_domain
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

#: The unique index a duplicate violates.
_SLUG_INDEX = 'organizations_slug_key'


class OrganizationDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """Database operations for organizations."""

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
                .where(OrgMembership.user_id == user_id, OrgMembership.status == 'active')
                .order_by(Organization.created_at)
            )
            return [(row[0], row[1]) for row in result.all()]

    async def all_with_member_counts(self) -> list[tuple[Organization, int]]:
        """Every organisation and how many active members it has, oldest first."""
        async with self.session_scope() as session:
            members = (
                select(OrgMembership.org_id, func.count().label('members'))
                .where(OrgMembership.status == 'active')
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
        display_name: str,
        slug: str,
        owner_user_id: str,
        domains: list[str],
        owner_email: str | None = None,
    ) -> Organization:
        """Create an organisation owning ``domains``. 409 for a taken slug or domain.

        The creator becomes its admin only when their own address is on
        one of its domains: a person belongs to the organisation owning
        their domain and no other. Otherwise the first person to join
        administers it (`Admission.admit`) — the case of a super admin
        creating a second organisation, or the bootstrap account
        creating the first under an address it will move off.
        """
        creator_joins = email_domain(owner_email) in set(domains)
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

            if creator_joins and not await self._member_anywhere(session, owner_user_id):
                session.add(
                    OrgMembership(
                        id=str(uuid.uuid4()),
                        org_id=org.id,
                        user_id=owner_user_id,
                        role='admin',
                        status='active',
                        created_at=now,
                        updated_at=now,
                    )
                )
            try:
                await session.flush()
            except IntegrityError as exc:
                # A taken slug was a 500 — on the org-creation form, which
                # is close to the first thing a new user does. Verified
                # against a running server: the second create with the same
                # slug answered 500 both sequentially and under a race
                # (8 concurrent creates gave one 201 and seven 500s).
                #
                # Only ``organizations.slug`` can realistically be the
                # cause here: ``org.id`` and the membership row both carry
                # a fresh uuid4. That is checked rather than assumed, so a
                # constraint added later still surfaces as a 500 somebody
                # investigates instead of a 409 that names the wrong thing.
                # Matched on the index name rather than a substring of the
                # driver's message — the latter was organizations.slug is NOT NULL, so `null value in column "slug"`
                # also contains the word and would have been reported as a
                # duplicate slug.
                if _SLUG_INDEX not in str(getattr(exc, 'orig', exc)):
                    raise
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"The name '{slug}' is already taken. Pick a different one.",
                ) from exc
            await OrgDomainDB().claim(session, str(org.id), domains)
            return org

    async def ensure_exists(
        self,
        org_id: str,
        slug: str,
        display_name: str,
        owner_user_id: str,
    ) -> None:
        """Create the org and owner membership if they don't already exist."""
        async with self.session_scope() as session:
            existing = (await session.execute(select(Organization).where(Organization.id == org_id))).scalars().first()
            if existing:
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
            # One organisation per person: the dev account already in one is not added to another.
            if not await self._member_anywhere(session, owner_user_id):
                session.add(
                    OrgMembership(
                        id=str(uuid.uuid4()),
                        org_id=org_id,
                        user_id=owner_user_id,
                        role='admin',
                        status='active',
                        created_at=now,
                        updated_at=now,
                    )
                )
            await session.flush()

    @staticmethod
    async def _member_anywhere(session: AsyncSession, user_id: str) -> bool:
        """Whether this person is in an organisation already: one at a time (migration 0022)."""
        found = await session.execute(
            select(OrgMembership.id).where(
                OrgMembership.user_id == user_id, OrgMembership.status.in_(('active', 'deactivated'))
            )
        )
        return found.first() is not None

    @staticmethod
    def _slugify(value: str) -> str:
        """Convert a string to a URL-safe slug."""
        slug = value.split('@')[0].lower().strip()
        slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')
        return slug[:32] or 'org'

    async def delete(self, org_id: str) -> None:
        """Delete an organization and all its memberships.

        Its own database is kept, detached: nothing routes to it again,
        and an operator can still read or drop it. Every approved service
        is owed the news, in the same transaction
        (`OrganisationDeletionNoticeDB.owe`); it is delivered afterwards.
        """
        async with self.session_scope() as session:
            await session.execute(OrgMembership.__table__.delete().where(OrgMembership.org_id == org_id))
            await session.execute(Organization.__table__.delete().where(Organization.id == org_id))
            await OrganisationDeletionNoticeDB.owe(session, [org_id], now=datetime.now(timezone.utc))
            await session.flush()
        await Tenants().detach(org_id)

    async def update(self, org_id: str, display_name: str | None = None, slug: str | None = None) -> Organization:
        async with self.session_scope() as session:
            result = await session.execute(select(Organization).where(Organization.id == org_id))
            org = result.scalars().first()
            if not org:
                raise ValueError('Organization not found')
            if display_name is not None:
                org.display_name = display_name  # type: ignore[assignment]
            if slug is not None:
                org.slug = slug  # type: ignore[assignment]
            org.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            await session.flush()
            return org
