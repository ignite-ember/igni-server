"""What is worth telling an admin about an entry they just wrote.

Advisory rather than a refusal — see `agent_tools` for why a copy of
igni's tool list must not be allowed to block a name it has not heard of
yet. The same reasoning covers a hook naming a script that is not in the
group yet: entries are saved one at a time, so refusing would make the
order they are created in matter.
"""

from __future__ import annotations

from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.models.org_group_entry import OrgGroupEntry
from app.services.portal.agent_names import (
    frontmatter_name as agent_frontmatter_name,
    warning_for as agent_name_warning,
)
from app.services.portal.agent_tools import warning_for as agent_tool_warning
from app.services.portal.hook_scripts import warning_for as hook_script_warning


class _EntryWarningProvider:
    """The warnings for one kind, given what else the group holds."""

    def warnings(self, *, content: str, siblings: list[OrgGroupEntry], entry_name: str | None) -> list[str]:
        raise NotImplementedError


class _AgentEntryWarnings(_EntryWarningProvider):
    def warnings(self, *, content: str, siblings: list[OrgGroupEntry], entry_name: str | None) -> list[str]:
        """The name first, then the tools.

        A tool typo degrades one agent, but a duplicate or placeholder
        ``name:`` makes the whole entry disappear, so it is the more
        useful thing to read first. *entry_name* is the entry being
        written: it is saved before this is asked, so it is among the
        siblings and must not collide with itself.
        """
        others = {
            name
            for e in siblings
            if str(e.kind) == 'agents'
            and str(e.entry_name) != entry_name
            and (name := agent_frontmatter_name(str(e.content)))
        }
        found = [agent_name_warning(content, other_names=others), agent_tool_warning(content)]
        return [warning for warning in found if warning]


class _HookEntryWarnings(_EntryWarningProvider):
    def warnings(self, *, content: str, siblings: list[OrgGroupEntry], entry_name: str | None) -> list[str]:
        """A hook naming a script the group does not ship."""
        available = {str(e.entry_name) for e in siblings if str(e.kind) == 'scripts'}
        script_warning = hook_script_warning(content, available_scripts=available)
        return [script_warning] if script_warning else []


class EntryWarnings:
    """Advisory warnings for an entry, by its kind."""

    def __init__(self, *, entries: GroupEntriesDB | None = None) -> None:
        self._entries = entries or GroupEntriesDB()
        self._providers: dict[str, _EntryWarningProvider] = {
            'agents': _AgentEntryWarnings(),
            'hooks': _HookEntryWarnings(),
        }

    async def warnings_for(
        self, *, kind: str, content: str, org_id: str, group_id: str, entry_name: str | None = None
    ) -> list[str]:
        """Anything worth telling the admin about *content*; empty for kinds with nothing to say."""
        provider = self._providers.get(kind)
        if provider is None:
            return []
        siblings = await self._entries.list_entries(org_id, group_id)
        return provider.warnings(content=content, siblings=siblings, entry_name=entry_name)
