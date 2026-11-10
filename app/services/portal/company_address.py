"""The bootstrap account's move onto an address on its company's domain."""

from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException, status

from app.api.base_v1.schema.portal.user import PortalAccount, UserMeResponse
from app.db.sql.org_domains import OrgDomainDB
from app.db.sql.schemas.org_domain import NotACompanyAddress
from app.db.sql.user import UserDB
from app.services.auth.auth_emails import AuthEmails
from app.services.auth.otp_service import OTPService
from app.services.identity.account_refusals import AccountRefusals
from app.services.portal.user_service import UserService


class CompanyAddress:
    """Proves a company address for an account in no organisation, then moves it there.

    Only for an account in no organisation: everybody else's address
    already decides which organisation they are in.
    """

    def __init__(
        self,
        *,
        domains: OrgDomainDB | None = None,
        users: UserDB | None = None,
        otp: OTPService | None = None,
        emails: AuthEmails | None = None,
        profiles: UserService | None = None,
    ) -> None:
        self._domains = domains or OrgDomainDB()
        self._users = users or UserDB()
        self._otp = otp or OTPService()
        self._emails = emails or AuthEmails()
        self._profiles = profiles or UserService()

    async def send_code(self, bg: BackgroundTasks, account: PortalAccount, email: str) -> str:
        """Send a code proving `email`; the message to show the caller.

        Checked here as well as at confirmation, so nobody is sent a code
        that could never work.
        """
        if account.org_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='Your address decides your organisation, so it is not changed here.',
            )
        address = email.strip()
        if await self._domains.org_for_address(address) is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(NotACompanyAddress()))
        return await self._emails.send_company_address_code(bg, address)

    async def adopt(self, account: PortalAccount, email: str, code: str) -> UserMeResponse:
        """Move the account onto the proven address and into the organisation that owns it.

        The old address is gone afterwards, and the account is that
        organisation's admin if it has none yet.
        """
        address = email.strip()
        await self._otp.validate_address_code(address, code)
        try:
            with AccountRefusals():
                joined = await self._users.move_to_company_address(account.user_id, address)
        except NotACompanyAddress as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        moved = account.model_copy(update={'email': address, 'org_id': str(joined.id), 'org_role': None})
        return await self._profiles.get_me(moved)
