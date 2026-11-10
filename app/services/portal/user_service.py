"""Service layer for portal user profile management."""

import logging

from alembic.util.exc import CommandError
from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.api.base_v1.schema.portal.user import (
    OrgSummary,
    PortalAccount,
    UpdateProfileRequest,
    UserMeResponse,
)
from app.db.sql.account_deletion import AccountDeletionDB
from app.db.sql.group_person_erasure import GroupPersonErasureDB
from app.db.sql.organizations import OrganizationDB
from app.db.sql.schemas.account_errors import LastSuperAdmin
from app.db.sql.schemas.tenancy import OrganisationDetached
from app.db.sql.team import TeamDB
from app.db.sql.tenant_registry import TenantRegistryDB
from app.db.sql.user import UserDB
from app.services.cloud.producer_feed import ProducerFeed
from app.services.identity.account_refusals import AccountRefusals
from app.services.identity.organisation_deletion_wake import DeletionWake, ProcessDeletionWake

logger = logging.getLogger(__name__)


class UserService:
    """Business logic for user profile operations.

    Five DB handles were module-level instances that every method
    reached for, and the class was a namespace of `@staticmethod`s
    over them — so nothing here could be exercised against a
    substitute without patching the module. They arrive through
    `__init__` now.

    """

    def __init__(
        self,
        *,
        users: UserDB | None = None,
        orgs: OrganizationDB | None = None,
        team: TeamDB | None = None,
        deletions: AccountDeletionDB | None = None,
        tenants: TenantRegistryDB | None = None,
        group_erasure: GroupPersonErasureDB | None = None,
        deletion_wake: DeletionWake | None = None,
    ) -> None:
        self._user_db = users or UserDB()
        self._org_db = orgs or OrganizationDB()
        self._team_db = team or TeamDB()
        self._deletions = deletions or AccountDeletionDB()
        self._tenants = tenants or TenantRegistryDB()
        self._group_erasure = group_erasure or GroupPersonErasureDB()
        self._deletion_wake = deletion_wake or ProcessDeletionWake()

    async def get_me(self, current_user: PortalAccount) -> UserMeResponse:
        """Return the current user's profile with org context."""
        user = await self._user_db.get_by_user_id(current_user.user_id)

        user_orgs = await self._org_db.list_user_orgs(current_user.user_id)
        orgs = [
            OrgSummary(
                id=org.id,
                slug=org.slug,
                display_name=org.display_name,
                role=mem.role,
            )
            for org, mem in user_orgs
        ]

        # Current org details
        current_org = await self._org_db.get_by_id(current_user.org_id) if current_user.org_id else None

        return UserMeResponse(
            user_id=current_user.user_id,
            email=current_user.email,
            full_name=current_user.full_name,
            role=current_user.org_role,
            created_at=user.created_at if user else None,
            pending_email=None,  # TODO: re-add when profile fields move to users table
            auth_provider=str(user.auth_provider) if user and user.auth_provider else 'email',
            org_id=current_user.org_id,
            org_slug=current_org.slug if current_org else None,
            org_display_name=current_org.display_name if current_org else None,
            organizations=orgs,
            is_super_admin=current_user.is_super_admin,
        )

    async def update_me(self, body: UpdateProfileRequest, current_user: PortalAccount) -> UserMeResponse:
        """Update the current user's profile fields."""
        if body.full_name is not None:
            with AccountRefusals():
                await self._user_db.update_full_name(current_user.user_id, body.full_name)

        return await self.get_me(current_user)

    async def delete_account(self, current_user: PortalAccount) -> None:
        """Delete the user's account and clean up memberships.

        - Removes the user from all orgs
        - Deletes orgs where they were the sole member
        - Refuses if that would orphan an org with members and no other
          admin
        - Deletes the user record

        **What survives, and why.** The word "permanently" used to be in
        this docstring and it was not accurate.

        *Provenance goes null.* ``org_groups.created_by_user_id`` and its
        three siblings are ``ON DELETE SET NULL``, so a group, an entry, a
        group membership or an inference node made by this person stays
        and stops naming them. Until that was changed, those columns were
        ``NOT NULL`` with ``NO ACTION`` and this method raised a
        foreign-key violation for anybody who had done admin work and then
        left an org that survived — a 500 on "delete my account".

        *Group membership goes.* ``org_group_members.user_id`` is
        ``CASCADE``, which is right: that row *is* the membership.

        *Usage history stays.* ``chat_usage`` has no foreign key to
        ``users``, so rows keyed to this user id remain after deletion.
        That is deliberate to the extent that billing records normally
        outlive accounts — but it is a retention decision rather than a
        technical necessity, and anybody who needs deletion to mean
        deletion should either anonymise those rows here or say in the
        product that usage history is kept.
        """
        user_orgs = await self._org_db.list_user_orgs(current_user.user_id)

        # Decided first, written second. The policy below needs
        # membership roles; the writing does not, and it has to be
        # all-or-nothing — so this loop only sorts the organisations
        # into two lists and refuses where it must.
        delete_org_ids: list[str] = []
        leave_org_ids: list[str] = []

        for org, mem in user_orgs:
            if mem.role == 'admin':
                # Check if there are other admins
                members, _ = await self._team_db.list_members(str(org.id))
                other_admins = [
                    m
                    for m in members
                    if m.role == 'admin' and m.user_id != current_user.user_id and m.status == 'active'
                ]
                other_members = [
                    m for m in members if m.user_id != current_user.user_id and m.status in ('active', 'invited')
                ]

                if other_members and not other_admins:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f'Transfer admin role in "{org.display_name}" before deleting your account.',
                    )

                # Sole member — the org goes with them.
                if not other_members:
                    delete_org_ids.append(str(org.id))
                    continue

            leave_org_ids.append(str(org.id))

        # One transaction (see `AccountDeletionDB`), then each
        # organisation's own database, which it cannot reach.
        try:
            async with ProducerFeed().removal():
                await self._deletions.remove(
                    user_id=current_user.user_id,
                    delete_org_ids=delete_org_ids,
                    leave_org_ids=leave_org_ids,
                )
                await self._clear_from_organisations(
                    user_id=current_user.user_id, deleted=delete_org_ids, left=leave_org_ids
                )
        except LastSuperAdmin as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='You are the only super admin. Make somebody else one before deleting your account.',
            ) from exc
        if delete_org_ids:
            self._deletion_wake.nudge()
        logger.info('Account deleted: %s', current_user.user_id)

    async def _clear_from_organisations(self, *, user_id: str, deleted: list[str], left: list[str]) -> None:
        """After the account is gone: stop routing to deleted organisations, clear it from the ones it left.

        The account is already deleted, so a failure clearing it from an
        organisation's groups leaves a stale group row for nobody, never a
        person who can still sign in — the outcome the single transaction
        exists to rule out. Logged and carried on past rather than
        answered as a failed deletion, and caught here so the feed drain
        and deletion nudge after it still run: the database failing, the
        organisation deleted by another admin meanwhile, or its database
        failing to provision or migrate on the way in.
        """
        for org_id in deleted:
            await self._tenants.detach(org_id)
        for org_id in left:
            try:
                await self._group_erasure.forget_person(org_id, user_id=user_id)
            except (SQLAlchemyError, OSError, OrganisationDetached, CommandError):
                logger.exception('Could not clear a deleted account from organisation %s', org_id)
