"""What a CLI receives from the group its person is in."""

from __future__ import annotations

from datetime import datetime, timezone

from app.api.base_v1.schema.portal.group_entries import OrgGroupPackEntry
from app.api.base_v1.schema.portal.group_theme import GroupTheme
from app.api.base_v1.schema.portal.groups import OrgGroupPack
from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.groups import GroupsDB
from app.services.portal.default_group import DefaultGroup


class GroupPackService:
    """Builds the pack of enabled entries, model and theme a person's group gives them."""

    def __init__(
        self,
        *,
        groups: GroupsDB | None = None,
        entries: GroupEntriesDB | None = None,
        default_group: DefaultGroup | None = None,
    ) -> None:
        self._groups = groups or GroupsDB()
        self._entries = entries or GroupEntriesDB()
        self._default_group = default_group or DefaultGroup()

    async def get_my_group_pack(self, *, user_id: str, org_id: str | None = None) -> OrgGroupPack | None:
        """The full pack of entries for the group the user belongs to.

        A user in no group is put in their org's default one, which is
        created and seeded with the shipped agents the first time anyone
        asks. Done here rather than at signup so the orgs that already
        exist are covered too, and here rather than in
        `GroupModelService.model_for_user` because this path runs once per
        CLI start, not once per chat request.

        `org_id` is the organisation the session is in (token claim, else
        header, else first membership — `get_current_account`). A person in
        two organisations gets the pack for the one they are signed into.
        """
        summary = await self._default_group.ensure_for_user(user_id, org_id)
        if not summary:
            return None

        group = await self._groups.get_group(summary.org_id, summary.id)
        if not group:
            return None

        entries = await self._entries.list_entries(summary.org_id, summary.id)
        return OrgGroupPack(
            group_id=group.id,
            group_name=group.name,
            fetched_at=datetime.now(timezone.utc),
            entries=[OrgGroupPackEntry.of(entry) for entry in entries if entry.enabled],
            default_model=group.default_model,
            theme=GroupTheme.from_column(group.theme),
            org_id=summary.org_id,
        )
