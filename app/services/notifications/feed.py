"""Reading somebody's notifications, counting them, and marking them read.

What the igni host on a person's machine calls with their session
(``docs/service-identity.md`` §10.1). Listing is delivery: what is
answered is marked delivered.

**Reads must be allowed to fail.** Nothing here is wrapped in anything:
a feed that answered "nothing is waiting" during a database outage would
be indistinguishable from an empty one, so a failed read is a 500.
"""

from __future__ import annotations

from app.db.sql.notifications import MAX_NOTIFICATIONS, NotificationsDB
from app.db.sql.schemas.notification import NotificationCounts, NotificationRef


class NotificationFeed:
    """One account's notifications, from every organisation it is an active member of."""

    #: The most one call may ask for at once.
    MAX_PAGE: int = MAX_NOTIFICATIONS

    def __init__(self, *, notifications: NotificationsDB | None = None) -> None:
        self._notifications = notifications or NotificationsDB()

    @classmethod
    def default(cls) -> NotificationFeed:
        """The instance a route gets from ``Depends``."""
        return cls()

    async def listing(
        self, *, user_id: str, unread_only: bool = False, source: str = '', limit: int = MAX_PAGE
    ) -> list[NotificationRef]:
        """Newest first, marked delivered. ``limit`` is clamped, not trusted."""
        return await self._notifications.deliver(
            user_id=user_id,
            unread_only=unread_only,
            source=source,
            limit=max(1, min(limit, self.MAX_PAGE)),
        )

    async def counts(self, *, user_id: str) -> NotificationCounts:
        """Unread, in all and per extension — counted, not measured off the list."""
        return await self._notifications.counts(user_id=user_id)

    async def mark_read(self, *, user_id: str, ids: list[str] | None = None, source: str = '') -> int:
        """Mark these, one extension's, or everything read. Answers how many changed."""
        return await self._notifications.mark_read(user_id=user_id, ids=ids, source=source)
