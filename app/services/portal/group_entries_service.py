"""What a group holds: its agents, skills, MCP servers, plugins and the rest."""

from __future__ import annotations

from app.api.base_v1.schema.portal.group_entries import (
    CreateEntryRequest,
    OrgGroupEntryList,
    OrgGroupEntryResponse,
    UpdateEntryRequest,
)
from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.models.org_group_entry import OrgGroupEntry
from app.db.sql.schemas.group import EntryNotFound
from app.services.portal.entry_rules import EntryRules
from app.services.portal.group_access import GroupAccess
from app.services.portal.served_models import ServedModels


class GroupEntriesService:
    """Reading and writing a group's entries, scoped to the org that owns it."""

    def __init__(
        self,
        *,
        entries: GroupEntriesDB | None = None,
        access: GroupAccess | None = None,
        served: ServedModels | None = None,
        rules: EntryRules | None = None,
    ) -> None:
        self._entries = entries or GroupEntriesDB()
        self._access = access or GroupAccess()
        self._served = served or ServedModels()
        self._rules = rules or EntryRules()

    async def list_entries(self, *, org_id: str, group_id: str) -> OrgGroupEntryList:
        """Every entry of a group in *org_id*."""
        await self._access.in_org(org_id=org_id, group_id=group_id)
        entries = [OrgGroupEntryResponse.of(entry) for entry in await self._entries.list_entries(org_id, group_id)]
        return OrgGroupEntryList(entries=entries, total=len(entries))

    async def get_entry(self, *, org_id: str, group_id: str, entry_id: str) -> OrgGroupEntry:
        """One entry, scoped to the org that owns its group."""
        await self._access.in_org(org_id=org_id, group_id=group_id)
        entry = await self._entries.get_entry(org_id, group_id=group_id, entry_id=entry_id)
        if not entry:
            raise EntryNotFound()
        return entry

    async def create_entry(
        self, *, org_id: str, group_id: str, body: CreateEntryRequest, by_user_id: str
    ) -> OrgGroupEntryResponse:
        """A new entry, once the group is writable and the request meets every rule.

        In this order, so the first thing wrong is the thing reported:
        the group, the kind, a model on the kind, whether that model is
        served, the content type, then the plugin source.
        """
        await self._access.writable(org_id=org_id, group_id=group_id)
        self._rules.refuse_bad_kind(body)
        await self._served.refuse_unserved(body.model)
        new_entry = self._rules.entry_from(body)

        entry = await self._entries.create_entry(org_id, group_id=group_id, entry=new_entry, by_user_id=by_user_id)
        return OrgGroupEntryResponse.of(entry)

    async def update_entry(
        self, *, org_id: str, group_id: str, entry_id: str, body: UpdateEntryRequest
    ) -> OrgGroupEntryResponse:
        """Change what *body* supplies. A model is held to the same rules as on create; the kind is the row's."""
        await self._access.writable(org_id=org_id, group_id=group_id)
        if body.model:
            existing = await self._entries.get_entry(org_id, group_id=group_id, entry_id=entry_id)
            if existing:
                self._rules.refuse_model_on(str(existing.kind))
            await self._served.refuse_unserved(body.model)

        entry = await self._entries.update_entry(org_id, group_id=group_id, entry_id=entry_id, patch=body.to_patch())
        return OrgGroupEntryResponse.of(entry)

    async def delete_entry(self, *, org_id: str, group_id: str, entry_id: str) -> None:
        """Delete an entry of a writable group."""
        await self._access.writable(org_id=org_id, group_id=group_id)
        await self._entries.delete_entry(org_id, group_id=group_id, entry_id=entry_id)
