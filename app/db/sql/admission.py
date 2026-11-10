"""Who may have an account at all, and which organisation it joins as it is made.

A self-hosted install holds one or two organisations, and its people are
the company's: a person belongs to the organisation that owns their
address's domain, and to no other. So:

* an account may only be made for an address on a claimed domain, and
  it joins that organisation as it is made;
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
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql.models.org_domain import OrgDomain
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.models.user import User
from app.db.sql.schemas.org_domain import NotACompanyAddress, NotSetUpYet
from app.db.sql.schemas.pending_invites import PendingInvites
from app.security.account_domains import email_domain


class Admission:
    """Admission decided inside the caller's transaction.

    Made by `UserDB.create`, the one place every way of making an
    account goes through — password sign-up, OAuth and OIDC, and SCIM —
    over its session and under its lock, so two first sign-ups cannot
    both be the bootstrap account.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def owner_of(self, email: str | None) -> Organization | None:
        """The organisation owning this address's domain, or ``None``."""
        domain = email_domain(email)
        if not domain:
            return None
        result = await self._session.execute(
            select(Organization).join(OrgDomain, OrgDomain.org_id == Organization.id).where(OrgDomain.domain == domain)
        )
        return result.scalars().first()

    async def joining(self, email: str) -> Organization | None:
        """The organisation this address will join, or ``None`` for the bootstrap account.

        `NotACompanyAddress` once an organisation exists and owns none of
        it; `NotSetUpYet` while the bootstrap account has not made one.
        """
        owner = await self.owner_of(email)
        if owner is not None:
            return owner
        if (await self._session.execute(select(Organization.id).limit(1))).first() is not None:
            raise NotACompanyAddress()
        if (await self._session.execute(select(User.user_id).limit(1))).first() is not None:
            raise NotSetUpYet()
        return None

    async def admit(self, *, org: Organization, user_id: str, email: str) -> OrgMembership:
        """Make this account a member of the organisation owning its domain.

        A pending invitation to that organisation is claimed, so the
        role an admin chose stands. Otherwise they join as a member —
        or as admin, when the organisation has none yet: the first
        person to join an organisation administers it.
        """
        now = datetime.now(timezone.utc)
        pending = await self._pending_invites(org_id=str(org.id), email=email)
        if pending:
            keep, superseded = PendingInvites(pending).newest_per_org()
            for row in superseded:
                await self._session.delete(row)
            await self._session.flush()
            claimed = keep[str(org.id)]
            claimed.user_id = user_id
            claimed.status = 'active'
            claimed.updated_at = now
            await self._session.flush()
            return claimed

        member = OrgMembership(
            id=str(uuid.uuid4()),
            org_id=org.id,
            user_id=user_id,
            role='member' if await self._has_admin(str(org.id)) else 'admin',
            status='active',
            created_at=now,
            updated_at=now,
        )
        self._session.add(member)
        await self._session.flush()
        return member

    async def _pending_invites(self, *, org_id: str, email: str) -> list[OrgMembership]:
        result = await self._session.execute(
            select(OrgMembership).where(
                OrgMembership.org_id == org_id,
                func.lower(OrgMembership.invited_email) == email.strip().casefold(),
                OrgMembership.status == 'invited',
            )
        )
        return list(result.scalars().all())

    async def _has_admin(self, org_id: str) -> bool:
        found = await self._session.execute(
            select(OrgMembership.id)
            .where(OrgMembership.org_id == org_id, OrgMembership.role == 'admin', OrgMembership.status == 'active')
            .limit(1)
        )
        return found.first() is not None
