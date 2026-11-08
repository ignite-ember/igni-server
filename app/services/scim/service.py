"""Provisioning, as the identity provider understands it.

A thin layer, on purpose. Everything underneath already exists:
creating an account is the path every sign-up goes through,
deprovisioning is the portal's own deactivation (``MemberStatus``) —
the account, which withdraws the person's sessions in the same
statement, and the membership, which is what the Team page shows —
and the door refuses them from the next request. SCIM adds the
vocabulary an IdP speaks and nothing else.

**``active: false`` is the whole point.** It is how Okta and Entra say
somebody left, and it is the event that used to arrive nowhere: an
administrator removed a person from the directory and igni carried on
serving them until a human remembered to go and deactivate them by
hand.

**Delete is treated as deactivate.** Providers differ — some send
``DELETE``, some only ever set ``active: false`` — and the two must
mean the same thing here, because a provider's choice of verb should
not decide whether a departed employee's documents survive. Removing
the row would take their work with it, which no directory operation
should do silently; `DELETE /v1/portal/me` is the deliberate path for
that and it is a person's own decision.

**Matching is by address.** SCIM has ``externalId`` for the provider's
own key, and it is echoed rather than stored: an account here is
identified by its email, every other sign-in path keys on the same
thing, and a second identity space would be a second thing that can
disagree about who somebody is.
"""

from __future__ import annotations

import logging
import secrets

import bcrypt

from app.api.base_v1.schema.scim import ScimUser
from app.db.sql.models.user import User
from app.db.sql.org_domains import OrgDomainDB
from app.db.sql.schemas.org_domain import AdmissionRefused
from app.db.sql.user import UserDB
from app.services.portal.member_status import MemberStatus
from app.services.scim.refusals import OffEveryDomain

logger = logging.getLogger(__name__)


class ScimService:
    """The accounts a provider looks up, creates, and deactivates."""

    def __init__(
        self,
        *,
        users: UserDB | None = None,
        domains: OrgDomainDB | None = None,
        member_status: MemberStatus | None = None,
    ) -> None:
        self._users = users or UserDB()
        self._domains = domains or OrgDomainDB()
        self._member_status = member_status or MemberStatus()

    async def organisation_of(self, address: str) -> str | None:
        """The organisation a person at *address* belongs to: whose audit trail records what SCIM did to them."""
        org = await self._domains.org_for_address(address) if address else None
        return str(org.id) if org is not None else None

    async def find_by_address(self, address: str) -> User | None:
        return await self._users.get_by_email(address) if address else None

    async def find_by_id(self, user_id: str) -> User | None:
        return await self._users.get_by_user_id(user_id)

    async def provision(self, payload: ScimUser) -> User:
        """Create the account a provider is telling us about.

        The password is random and discarded. An account provisioned from
        a directory signs in through that directory; giving it a usable
        local password would create a second way in that the directory
        cannot take away, which is the whole thing SCIM is here to fix.

        Raises :class:`OffEveryDomain` before an account exists — the
        same rule `UserDB.create` applies, said in words a provider
        shows — and the account store's own refusals untouched.
        """
        address = payload.address
        if await self._domains.org_for_address(address) is None:
            raise OffEveryDomain(address)

        throwaway = bcrypt.hashpw(secrets.token_urlsafe(32).encode(), bcrypt.gensalt()).decode()
        try:
            user = await self._users.create(
                address,
                hashed_password=throwaway,
                full_name=payload.full_name or None,
                auth_provider='scim',
            )
        except AdmissionRefused as exc:
            # The domain was released between the check above and the insert.
            raise OffEveryDomain(address) from exc
        # A provider may create somebody already deactivated — a leaver
        # synced for the first time. Honoured rather than assumed active.
        if not payload.active:
            await self._member_status.set_everywhere(user.user_id, active=False)
            # Re-read so the response carries the deactivated flag. Kept
            # only if it finds the row: the account was created moments
            # ago, so a miss means something removed it concurrently, and
            # answering `None` from a provisioning call that succeeded is
            # worse than answering with the row we already hold.
            refreshed = await self._users.get_by_user_id(user.user_id)
            if refreshed is not None:
                user = refreshed
        logger.info('scim: provisioned %s', address)
        return user

    async def set_active(self, user_id: str, *, active: bool) -> User | None:
        """The deprovisioning lever, and the thing SCIM is here for.

        Exactly what an org admin's deactivation and reactivation do, for
        each organisation the person is in — see :class:`MemberStatus`.
        Deactivating withdraws every session the person holds, in the same
        statement, so a directory removal stops them on their next request
        rather than when their token happens to expire.
        """
        await self._member_status.set_everywhere(user_id, active=active)
        logger.info('scim: %s %s', 'deactivated' if not active else 'reactivated', user_id)
        return await self._users.get_by_user_id(user_id)
