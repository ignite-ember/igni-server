"""Who is a super admin, read under the lock that serialises every change to it."""

from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql.models.user import User
from app.db.sql.schemas.account_errors import LastSuperAdmin


class SuperAdminRoster:
    """The super admins, inside the caller's transaction.

    Every change to who is a super admin — the first sign-up becoming
    one, making or unmaking one, an account deleting itself, the
    bootstrap account moving address — first takes a transaction-scoped
    advisory lock through :meth:`hold`. Without it two first sign-ups
    arriving together would both see "no super admin yet" and both
    become one, and two removals together could each see another super
    admin left and leave none.
    """

    #: The advisory lock key: "SUPER" in ASCII.
    LOCK = 0x5355504552

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def hold(self) -> None:
        """Take the lock, held until the caller's transaction ends."""
        await self._session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': self.LOCK})

    async def nobody(self) -> bool:
        """Whether this deployment has no super admin yet. Call after :meth:`hold`."""
        found = await self._session.execute(select(User.user_id).where(User.is_super_admin.is_(True)).limit(1))
        return found.first() is None

    async def refuse_losing_last(self, user: User | None) -> None:
        """Raise :class:`LastSuperAdmin` if *user* is the only super admin left. Call after :meth:`hold`."""
        if user is None or not user.is_super_admin:
            return
        result = await self._session.execute(
            select(func.count()).select_from(User).where(User.is_super_admin.is_(True))
        )
        if int(result.scalar_one()) <= 1:
            raise LastSuperAdmin(user.user_id)
