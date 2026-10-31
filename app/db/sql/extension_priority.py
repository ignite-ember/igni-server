"""Storing the priority order of extensions. See `app.db.sql.models.extension_priority`."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.extension_priority import ExtensionPriority
from app.db.sql.models.user import User
from app.utils.singleton import SingletonMeta

#: Who set it, when it was set with the break-glass key.
BREAK_GLASS = 'the break-glass key'
#: Who set it, once that account is gone: the address is read from the
#: account when shown, never copied, so erasing a person erases it here.
A_FORMER_SUPER_ADMIN = 'a former super admin'


class StoredPriority(BaseModel):
    """The order a super admin set, with who set it as a person reads it."""

    #: Extension ids, highest first.
    order: list[str] = Field(default_factory=list)
    set_by: str = ''
    set_at: datetime | None = None


class ExtensionPriorityDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def replace(self, order: list[str], *, set_by_user_id: str | None) -> StoredPriority:
        """The whole order, replacing the one before. No `set_by_user_id` means the break-glass key."""
        now = datetime.now(timezone.utc)
        async with self.session_scope() as session:
            await session.execute(delete(ExtensionPriority))
            session.add_all(
                ExtensionPriority(
                    extension_id=extension_id,
                    position=position,
                    set_by_user_id=set_by_user_id,
                    break_glass=set_by_user_id is None,
                    set_at=now,
                )
                for position, extension_id in enumerate(order)
            )
        return await self.saved()

    async def saved(self) -> StoredPriority:
        async with self.session_scope() as session:
            result = await session.execute(
                select(ExtensionPriority, User.email)
                .outerjoin(User, User.user_id == ExtensionPriority.set_by_user_id)
                .order_by(ExtensionPriority.position)
            )
            rows = result.all()
        if not rows:
            return StoredPriority()
        first, email = rows[0]
        return StoredPriority(
            order=[row.extension_id for row, _ in rows],
            set_by=email or (BREAK_GLASS if first.break_glass else A_FORMER_SUPER_ADMIN),
            set_at=first.set_at,
        )
