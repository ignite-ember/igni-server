"""The model a person's group pins, for a chat request that names none."""

from __future__ import annotations

import logging

from app.db.sql.group_summaries import GroupSummariesDB
from app.db.sql.groups import GroupsDB

logger = logging.getLogger(__name__)


class GroupModelService:
    """Resolves a person's group model on the chat hot path."""

    def __init__(self, *, groups: GroupsDB | None = None, summaries: GroupSummariesDB | None = None) -> None:
        self._groups = groups or GroupsDB()
        self._summaries = summaries or GroupSummariesDB()

    async def model_for_user(self, user_id: str) -> str | None:
        """The model this user's group pins, or None.

        Consulted on every chat request that does not name a model, so it
        must be cheap and must never fail one: a lookup that cannot be
        answered, for whatever reason, falls back to the deployment
        default, which would have served the request perfectly well. Not
        only a database that is down — an organisation detached meanwhile
        or anything else unforeseen costs the pin, never the request.

        Active membership only, like the pack: a group's pinned model is
        the organisation's configuration, and somebody who has been
        deactivated falls back to the deployment default like any other
        stranger.
        """
        try:
            summary = await self._summaries.get_active_user_group_summary(user_id)
            if not summary:
                return None
            group = await self._groups.get_group(summary.org_id, group_id=summary.id)
        except Exception as exc:  # noqa: BLE001 — routing must survive a group lookup, however it fails
            logger.warning('Could not resolve a group model for %s: %s', user_id, exc)
            return None
        return str(group.default_model) if group and group.default_model else None
