"""Which organisation an address belongs to, and who may have an account at all.

A self-hosted install holds one or two organisations, and its people are
the company's: a person belongs to the organisation that owns their
address's domain, and to no other. So:

* an organisation owns one or more domains, and no two share one;
* an account may only be made for an address on a claimed domain, and
  it joins that organisation as it is made (:class:`Admission`);
* the one exception is a fresh install with no organisation yet, whose
  first account may use any company address. It is the bootstrap
  account: it creates the organisation, then either moves itself onto
  an address on that organisation's domain or makes in-domain super
  admins and deletes itself.

Apps — a PR reviewer, anything that is not a person — are a different
kind of identity and are not accounts here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.org_domain import OrgDomain
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.models.user import User
from app.db.sql.team import newest_invite_per_org
from app.security.account_domains import email_domain, is_public_mail_domain
from app.utils.singleton import SingletonMeta

#: A hostname with at least one dot: labels of letters, digits and
#: hyphens, no hyphen at either end of a label.
_DOMAIN = re.compile(r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')

#: The unique index a second claim on a domain violates.
_DOMAIN_INDEX = 'idx_org_domains_domain'

#: Sign-up for an address on no organisation's domain.
NOT_A_COMPANY_ADDRESS = "This address isn't on a domain any organisation here uses. Sign up with your company address."
#: Sign-up before the first account has set the deployment up.
NOT_SET_UP_YET = (
    "This deployment isn't set up yet. Its first account creates the organisation; ask whoever installed it."
)


def normalise_domain(value: str) -> str:
    """A domain as stored: lowercased, no ``@``, no surrounding dots. 422 for anything else.

    A consumer mail domain is refused: ``gmail.com`` names no employer,
    and claiming it would make every Gmail user a member.
    """
    domain = value.strip().casefold().lstrip('@').strip('.')
    if not _DOMAIN.match(domain):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"'{value}' is not an email domain, like acme.com.",
        )
    if is_public_mail_domain(domain):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'{domain} is a public mail provider, not a company domain.',
        )
    return domain


class OrgDomainDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """The domains organisations own."""

    async def domains_of(self, org_id: str) -> list[str]:
        async with self.session_scope() as session:
            result = await session.execute(select(OrgDomain.domain).where(OrgDomain.org_id == org_id))
            # Sorted here, not by the database: its collation orders
            # `acme-uk.io` and `acmecorp.io` differently from Python.
            return sorted(str(domain) for domain in result.scalars().all())

    async def org_for_address(self, email: str | None) -> Organization | None:
        """The organisation owning this address's domain, or ``None``."""
        async with self.session_scope() as session:
            return await Admission.owner_of(session, email)

    async def claim(self, session: AsyncSession, org_id: str, domains: list[str]) -> None:
        """Give these domains to an organisation, inside the caller's transaction. 409 if any is taken."""
        for domain in domains:
            session.add(
                OrgDomain(id=str(uuid.uuid4()), org_id=org_id, domain=domain, created_at=datetime.now(timezone.utc))
            )
        try:
            await session.flush()
        except IntegrityError as exc:
            if _DOMAIN_INDEX not in str(getattr(exc, 'orig', exc)):
                raise
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='One of those domains already belongs to another organisation.',
            ) from exc

    async def add(self, org_id: str, domain: str) -> list[str]:
        """Add a domain to an organisation; returns its domains. 409 if another owns it. Idempotent."""
        async with self.session_scope() as session:
            existing = await session.execute(select(OrgDomain).where(OrgDomain.domain == domain))
            owned = existing.scalars().first()
            if owned is not None and owned.org_id != org_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f'{domain} already belongs to another organisation.',
                )
            if owned is None:
                await self.claim(session, org_id, [domain])
        return await self.domains_of(org_id)

    async def remove(self, org_id: str, domain: str) -> list[str]:
        """Release a domain. 409 while any member's address is on it; 404 if not theirs."""
        async with self.session_scope() as session:
            row = (
                (await session.execute(select(OrgDomain).where(OrgDomain.org_id == org_id, OrgDomain.domain == domain)))
                .scalars()
                .first()
            )
            if row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such domain here.')
            on_it = await session.execute(
                select(func.count())
                .select_from(OrgMembership)
                .join(User, User.user_id == OrgMembership.user_id)
                .where(OrgMembership.org_id == org_id, func.lower(User.email).like(f'%@{domain}'))
            )
            if on_it.scalar_one():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f'Members of this organisation use {domain}. Remove them first.',
                )
            await session.delete(row)
        return await self.domains_of(org_id)


class Admission:
    """Who may have an account, and which organisation they join as it is made.

    Called by `UserDB.create`, the one place every way of making an
    account goes through — password sign-up, OAuth and OIDC, and SCIM —
    inside its transaction and under its lock, so two first sign-ups
    cannot both be the bootstrap account.
    """

    @staticmethod
    async def owner_of(session: AsyncSession, email: str | None) -> Organization | None:
        domain = email_domain(email)
        if not domain:
            return None
        result = await session.execute(
            select(Organization).join(OrgDomain, OrgDomain.org_id == Organization.id).where(OrgDomain.domain == domain)
        )
        return result.scalars().first()

    @staticmethod
    async def check(session: AsyncSession, email: str) -> Organization | None:
        """The organisation this address will join, ``None`` for the bootstrap account, or 403."""
        owner = await Admission.owner_of(session, email)
        if owner is not None:
            return owner
        any_org = (await session.execute(select(Organization.id).limit(1))).first()
        if any_org is not None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_A_COMPANY_ADDRESS)
        any_user = (await session.execute(select(User.user_id).limit(1))).first()
        if any_user is not None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_SET_UP_YET)
        return None

    @staticmethod
    async def admit(session: AsyncSession, org: Organization, user_id: str, email: str) -> OrgMembership:
        """Make this account a member of the organisation owning its domain.

        A pending invitation to that organisation is claimed, so the
        role an admin chose stands. Otherwise they join as a member —
        or as admin, when the organisation has none yet: the first
        person to join an organisation administers it.
        """
        pending = (
            (
                await session.execute(
                    select(OrgMembership).where(
                        OrgMembership.org_id == org.id,
                        func.lower(OrgMembership.invited_email) == email.strip().casefold(),
                        OrgMembership.status == 'invited',
                    )
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(timezone.utc)
        if pending:
            keep, superseded = newest_invite_per_org(pending)
            for row in superseded:
                await session.delete(row)
            await session.flush()
            claimed = keep[str(org.id)]
            claimed.user_id = user_id
            claimed.status = 'active'
            claimed.updated_at = now
            await session.flush()
            return claimed

        has_admin = (
            await session.execute(
                select(OrgMembership.id)
                .where(OrgMembership.org_id == org.id, OrgMembership.role == 'admin', OrgMembership.status == 'active')
                .limit(1)
            )
        ).first()
        member = OrgMembership(
            id=str(uuid.uuid4()),
            org_id=org.id,
            user_id=user_id,
            role='member' if has_admin else 'admin',
            status='active',
            created_at=now,
            updated_at=now,
        )
        session.add(member)
        await session.flush()
        return member
