"""Which approved extensions a group's people get, and which a producer gets.

A super admin approves a *service* once — that is trust. An org admin
then decides which groups use its extension — that is enablement, and it
is off until they do (`docs/service-identity.md` §8.3). A person gets an
extension when it is on for their group — a person is in exactly one; igni server serves its
bundle and assets to them and to nobody else, and igni installs and
offers only those.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.base_v1.schema.portal.groups import GroupExtension, GroupExtensionList
from app.db.sql.apps import AppExtensionsDB
from app.db.sql.group_extensions import GroupExtensionsDB
from app.db.sql.groups import GroupsDB
from app.db.sql.models.app_space import SPACE
from app.db.sql.models.registered_client import APP
from app.db.sql.schemas.group import GroupSummary
from app.db.sql.service_extensions import ServiceExtensionDB
from app.db.sql.spaces import SpacesDB
from app.security.producer import ProducerCaller
from app.services.portal.default_group import DefaultGroup


class Entitlement(BaseModel):
    """What one producer gets: a person through their one group, an app through its own list."""

    model_config = ConfigDict(frozen=True)

    #: The person's group in the organisation they are signed in to;
    #: always null for an app, which is in no group.
    group: str | None = None
    #: That group's name, for a client to show (the id means nothing to a person).
    group_name: str | None = None
    #: Extension id → every group in the organisation it is on for,
    #: only for the extensions this producer gets.
    enabled: dict[str, list[str]] = Field(default_factory=dict)
    #: Extension id → every app in the organisation it is on for, for
    #: the same extensions.
    apps: dict[str, list[str]] = Field(default_factory=dict)

    def allows(self, extension_id: str) -> bool:
        return extension_id in self.enabled


class GroupExtensions:
    """Per-group enablement, and the entitlement it gives a person."""

    def __init__(
        self,
        *,
        enabled: GroupExtensionsDB | None = None,
        groups: GroupsDB | None = None,
        extensions: ServiceExtensionDB | None = None,
        default_group: DefaultGroup | None = None,
    ) -> None:
        self._enabled = enabled or GroupExtensionsDB()
        self._groups = groups or GroupsDB()
        self._extensions = extensions or ServiceExtensionDB()
        self._default_group = default_group or DefaultGroup(groups=self._groups)

    async def _group_in_org(self, org_id: str, group_id: str) -> None:
        if await self._groups.get_group(org_id, group_id=group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Group not found')

    async def of_group(self, org_id: str, group_id: str) -> GroupExtensionList:
        """Every approved extension, each with whether this group has it on."""
        await self._group_in_org(org_id, group_id)
        on = await self._enabled.enabled_in(org_id, group_id)
        return GroupExtensionList(
            extensions=[
                GroupExtension(
                    extension_id=extension.extension_id,
                    name=str(extension.manifest.get('name') or extension.extension_id),
                    description=str(extension.manifest.get('description') or ''),
                    service_url=client.base_url,
                    enabled=extension.extension_id in on,
                )
                for extension, client in await self._extensions.distributed()
            ]
        )

    async def set(self, org_id: str, group_id: str, extension_id: str, enabled: bool, by_user_id: str) -> None:
        """Switch an approved extension on or off for a group.

        The Default group can have extensions too: it is read-only as the
        copy of what igni ships, but whether its people get an extension
        is not part of that copy, and most people are in it.
        """
        await self._group_in_org(org_id, group_id)
        if enabled:
            if await self._extensions.archive_of(extension_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail='No approved service ships this extension'
                )
            await self._enabled.enable(org_id, group_id, extension_id, by_user_id)
        else:
            await self._enabled.disable(org_id, group_id, extension_id)

    async def of_person(self, user_id: str, org_id: str | None) -> Entitlement:
        """What a person signed in to *org_id* gets. Nothing without an organisation or a group."""
        summary = await self._group_summary_of(user_id, org_id)
        if summary is None or org_id is None:
            return Entitlement()
        group = summary.id
        every = await self._enabled.groups_enabling(org_id)
        apps = await AppExtensionsDB().apps_enabling(org_id)
        mine = [extension_id for extension_id, groups in every.items() if group in groups]
        return Entitlement(
            group=group,
            group_name=summary.name,
            enabled={extension_id: every[extension_id] for extension_id in mine},
            apps={extension_id: apps.get(extension_id, []) for extension_id in mine},
        )

    async def of_app(self, app_id: str, org_id: str) -> Entitlement:
        """What an app gets: the extensions on its own list. It is in no group."""
        apps = await AppExtensionsDB().apps_enabling(org_id)
        every = await self._enabled.groups_enabling(org_id)
        mine = [extension_id for extension_id, ids in apps.items() if app_id in ids]
        return Entitlement(
            enabled={extension_id: every.get(extension_id, []) for extension_id in mine},
            apps={extension_id: apps[extension_id] for extension_id in mine},
        )

    async def of_producer(self, caller: ProducerCaller) -> Entitlement:
        if caller.kind == APP and caller.org_id:
            return await self.of_app(caller.id, caller.org_id)
        if caller.kind == SPACE and caller.org_id:
            # A space has its app's extensions: it is one more VM of the app.
            space = await SpacesDB().get(caller.org_id, caller.id)
            return await self.of_app(space.app_id, caller.org_id) if space is not None else Entitlement()
        return await self.of_person(caller.id, caller.org_id)

    async def group_of(self, user_id: str, org_id: str | None) -> str | None:
        """The person's one group in this organisation — what their session token and the listing carry.

        The same path the group pack takes: somebody in no group yet is
        put in the organisation's Default one, so what they are offered,
        what their token says and what their pack holds come from the
        same group.
        """
        summary = await self._group_summary_of(user_id, org_id)
        return summary.id if summary is not None else None

    async def _group_summary_of(self, user_id: str, org_id: str | None) -> GroupSummary | None:
        if not org_id:
            return None
        summary = await self._default_group.ensure_for_user(user_id, org_id)
        if summary is None or str(summary.org_id) != str(org_id):
            return None
        return GroupSummary(id=str(summary.id), org_id=str(summary.org_id), name=summary.name)
