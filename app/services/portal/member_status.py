"""Deactivating a member and bringing them back: one path, whoever asks.

An org admin asks in the portal, for one membership; an identity
provider asks over SCIM, for a person (`active: false`). They are the
same act and used to be two halves of it: the portal set the membership
and the account, SCIM set only the account — so a person their directory
had deprovisioned was refused everywhere and still listed as active on
the Team page, which reads the membership.

Both now go through :class:`MemberStatus`, which sets the membership and
the account together and, on deactivation, stops the person's VM
credentials. Deactivating the account withdraws every session the person
holds in the same statement (:meth:`UserDB.set_deactivated`); bringing
them back re-admits none of them.
"""

from __future__ import annotations

from app.db.sql.team import TeamDB
from app.db.sql.user import UserDB
from app.services.cloud.vm_credentials import VmCredentials

ACTIVE = 'active'
DEACTIVATED = 'deactivated'


class MemberStatus:
    def __init__(
        self,
        *,
        team: TeamDB | None = None,
        users: UserDB | None = None,
        vm_credentials: VmCredentials | None = None,
    ) -> None:
        self._team = team or TeamDB()
        self._users = users or UserDB()
        self._vm_credentials = vm_credentials or VmCredentials()

    async def set_in_org(self, org_id: str, user_id: str, *, active: bool) -> str:
        """One membership, as an org admin sets it. Answers the status it had."""
        old_status = await self._team.update_status(org_id, user_id, ACTIVE if active else DEACTIVATED)
        await self._account(user_id, active=active)
        return old_status

    async def set_everywhere(self, user_id: str, *, active: bool) -> list[str]:
        """Every membership of the person, as their identity provider sets it. Answers the organisations changed.

        Only a membership that is active or deactivated moves: an
        invitation becomes a membership by being claimed, not by a
        directory saying the person is active.
        """
        changed = await self._team.move_status(
            user_id, from_status=DEACTIVATED if active else ACTIVE, to_status=ACTIVE if active else DEACTIVATED
        )
        await self._account(user_id, active=active)
        return changed

    async def _account(self, user_id: str, *, active: bool) -> None:
        """The account, which is what actually deprovisions: the membership alone is read by the portal only."""
        await self._users.set_deactivated(user_id, deactivated=not active)
        if not active:
            await self._vm_credentials.revoke_person(user_id)
