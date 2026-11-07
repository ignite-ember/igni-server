"""Removing a person and everything in the shared plane that is only theirs, in one transaction."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.models.user import User
from app.db.sql.organisation_deletion_notices import OrganisationDeletionNoticeDB
from app.db.sql.super_admin_roster import SuperAdminRoster
from app.utils.singleton import SingletonMeta


class AccountDeletionDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """The writing half of deleting an account, which has to be all-or-nothing.

    **One session, deliberately.** Spread across a `*DB` class per
    table, each opening its own `session_scope`, each step committed on
    its own, and a failure part-way left an account half-removed:
    organisations gone, memberships gone, and the person still able to
    sign in — the one outcome of a deletion request a person will not
    discover until they try.

    The policy stays in `UserService.delete_account` — *which* orgs are
    deleted versus left, and the refusal when leaving would orphan one —
    because it needs the membership roles and this does not. So does the
    cleanup in each organisation's own database, which this transaction
    cannot reach.
    """

    async def remove(self, *, user_id: str, delete_org_ids: list[str], leave_org_ids: list[str]) -> None:
        """Delete *delete_org_ids* outright, leave *leave_org_ids*, then delete the account.

        Raises :class:`~app.db.sql.schemas.account_errors.LastSuperAdmin`
        rather than take the deployment's last super admin with it.

        Ordered so the user row goes last: a failure at any earlier step
        rolls the whole transaction back, and the row that proves the
        request has not completed is the last thing to go.
        """
        async with self.session_scope() as session:
            roster = SuperAdminRoster(session)
            await roster.hold()
            leaving = (await session.execute(select(User).where(User.user_id == user_id))).scalars().first()
            await roster.refuse_losing_last(leaving)
            if delete_org_ids:
                # Memberships first: `organizations` has no cascade to
                # them, which is why `OrganizationDB.delete` deletes
                # both in that order too.
                await session.execute(OrgMembership.__table__.delete().where(OrgMembership.org_id.in_(delete_org_ids)))
                await session.execute(Organization.__table__.delete().where(Organization.id.in_(delete_org_ids)))
                # Every service owed the news, in this transaction: see `OrganizationDB.delete`.
                await OrganisationDeletionNoticeDB.owe(session, delete_org_ids, now=datetime.now(timezone.utc))
            if leave_org_ids:
                await session.execute(
                    OrgMembership.__table__.delete().where(
                        OrgMembership.org_id.in_(leave_org_ids),
                        OrgMembership.user_id == user_id,
                    )
                )
            await session.execute(User.__table__.delete().where(User.user_id == user_id))
            await session.flush()
