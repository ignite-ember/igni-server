"""Database layer for who owns the install."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.user import User
from app.db.sql.super_admin_roster import SuperAdminRoster
from app.utils.singleton import SingletonMeta


class SuperAdminDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """Listing super admins, and making or unmaking one."""

    async def super_admins(self) -> list[User]:
        """Everyone who may create organisations, oldest account first."""
        async with self.session_scope() as session:
            result = await session.execute(select(User).where(User.is_super_admin.is_(True)).order_by(User.created_at))
            return list(result.scalars().all())

    async def set_super_admin(self, user_id: str, *, super_admin: bool) -> User | None:
        """Make somebody a super admin, or stop them being one.

        ``None`` when there is no such account. Raises
        :class:`~app.db.sql.schemas.account_errors.LastSuperAdmin` rather
        than leave the deployment with none. Idempotent: setting what is
        already set changes nothing.
        """
        async with self.session_scope() as session:
            roster = SuperAdminRoster(session)
            await roster.hold()
            user = (await session.execute(select(User).where(User.user_id == user_id))).scalars().first()
            if user is None:
                return None
            if not super_admin:
                await roster.refuse_losing_last(user)
            user.is_super_admin = super_admin
            user.updated_at = datetime.now(timezone.utc)
            await session.flush()
            return user
