"""Withdrawing every session a person holds, everywhere it is honoured."""

from __future__ import annotations

from datetime import datetime

from app.db.sql.user import UserDB
from app.services.cloud.revocations import Revocations
from app.services.cloud.vm_credentials import VmCredentials
from app.services.identity.api_keys import ApiKeys


class SessionWithdrawal:
    """Refuses every token a person holds: theirs, their VMs' and their API keys'.

    The watermark on the user row is what igni server checks; their cloud
    VMs sign out too (docs/service-identity.md §16), and the gateway and
    services learn of it through the revocation feed (§21).
    """

    def __init__(
        self,
        *,
        users: UserDB | None = None,
        vm_credentials: VmCredentials | None = None,
        api_keys: ApiKeys | None = None,
        revocations: Revocations | None = None,
    ) -> None:
        self._users = users or UserDB()
        self._vm_credentials = vm_credentials or VmCredentials()
        self._api_keys = api_keys or ApiKeys()
        self._revocations = revocations or Revocations()

    async def withdraw(self, user_id: str) -> datetime:
        """Withdraw every session for `user_id`; returns the new watermark."""
        watermark = await self._users.withdraw_sessions(user_id)
        await self._vm_credentials.revoke_person(user_id)
        await self._api_keys.revoke_person(user_id)
        await self._revocations.sessions_withdrawn(user_id, watermark)
        return watermark
