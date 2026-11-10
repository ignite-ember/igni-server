"""The domains organisations own: one or more each, and no two sharing one.

A person belongs to the organisation owning their address's domain; who
may have an account at all is :class:`app.db.sql.admission.Admission`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.admission import Admission
from app.db.sql.models.org_domain import OrgDomain
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.models.user import User
from app.db.sql.schemas.org_domain import DomainInUse, DomainNotHeld, DomainTaken
from app.utils.singleton import SingletonMeta

#: The unique index a second claim on a domain violates.
_DOMAIN_INDEX = 'idx_org_domains_domain'


class OrgDomainDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """The domains organisations own. Domains arrive already checked (`CompanyDomain`)."""

    async def domains_of(self, org_id: str) -> list[str]:
        async with self.session_scope() as session:
            result = await session.execute(select(OrgDomain.domain).where(OrgDomain.org_id == org_id))
            # Sorted here, not by the database: its collation orders
            # `acme-uk.io` and `acmecorp.io` differently from Python.
            return sorted(str(domain) for domain in result.scalars().all())

    async def org_for_address(self, email: str | None) -> Organization | None:
        """The organisation owning this address's domain, or ``None``."""
        async with self.session_scope() as session:
            return await Admission(session).owner_of(email)

    async def claim(self, session: AsyncSession, *, org_id: str, domains: list[str]) -> None:
        """Give these domains to an organisation, inside the caller's transaction. `DomainTaken`."""
        for domain in domains:
            session.add(
                OrgDomain(id=str(uuid.uuid4()), org_id=org_id, domain=domain, created_at=datetime.now(timezone.utc))
            )
        try:
            await session.flush()
        except IntegrityError as exc:
            if _DOMAIN_INDEX not in str(getattr(exc, 'orig', exc)):
                raise
            raise DomainTaken() from exc

    async def add(self, org_id: str, domain: str) -> list[str]:
        """Add a domain to an organisation; returns its domains. Idempotent. `DomainTaken`."""
        async with self.session_scope() as session:
            existing = await session.execute(select(OrgDomain).where(OrgDomain.domain == domain))
            owned = existing.scalars().first()
            if owned is not None and owned.org_id != org_id:
                raise DomainTaken(domain)
            if owned is None:
                await self.claim(session, org_id=org_id, domains=[domain])
        return await self.domains_of(org_id)

    async def remove(self, org_id: str, domain: str) -> list[str]:
        """Release a domain; returns the organisation's domains. `DomainNotHeld`, `DomainInUse`."""
        async with self.session_scope() as session:
            row = (
                (await session.execute(select(OrgDomain).where(OrgDomain.org_id == org_id, OrgDomain.domain == domain)))
                .scalars()
                .first()
            )
            if row is None:
                raise DomainNotHeld(domain)
            on_it = await session.execute(
                select(func.count())
                .select_from(OrgMembership)
                .join(User, User.user_id == OrgMembership.user_id)
                .where(OrgMembership.org_id == org_id, func.lower(User.email).like(f'%@{domain}'))
            )
            if on_it.scalar_one():
                raise DomainInUse(domain)
            await session.delete(row)
        return await self.domains_of(org_id)
