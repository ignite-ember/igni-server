"""Whether a person has a cloud igni to use from the desktop, where its gateway is, and who owns their events.

igni server answers *whether*; the gateway answers *how it is right
now* (asleep, waking, running, signed in) on its own `GET /v1/me`, to a
token it already accepts. igni server never asks the gateway (igni-core
`docs/design/local-cloud-switch.md` §2).

The person is unavailable for the first of these that holds:

1. `fleet_not_configured` — this deployment has no Fleet gateway;
2. `not_licensed` — the installed licence lets Fleet start no VM;
3. `inactive` — they are not an active member, so the feed lists them
   inactive;
4. `group_local_only` — their group runs igni only on their computers.

igni server does not enforce the licence (`DeploymentLicence`): the
gateway does. It is read here so the desktop does not offer a switch the
gateway would refuse.
"""

from __future__ import annotations

from app.api.base_v1.schema.cloud_discovery import (
    CloudDiscovery,
    CloudDiscoveryChange,
    CloudGroup,
    CloudUnavailableReason,
)
from app.config import settings
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.models.org_group import LOCAL, LOCAL_AND_CLOUD
from app.db.sql.models.person_event_owner import EventsOwner
from app.db.sql.producer_feed import ProducerFeedDB
from app.db.sql.schemas.group import GroupSummary
from app.security.producer import ProducerCaller
from app.services.cloud.events_owner import EventOwners
from app.services.deployment_licence.service import DeploymentLicence
from app.services.licence import Module, Operation
from app.services.portal.default_group import DefaultGroup


class CloudDiscoveries:
    def __init__(
        self,
        licence: DeploymentLicence,
        *,
        default_group: DefaultGroup | None = None,
        members: GroupMembersDB | None = None,
        feed: ProducerFeedDB | None = None,
        owners: EventOwners | None = None,
    ) -> None:
        self._licence = licence
        self._default_group = default_group or DefaultGroup()
        self._members = members or GroupMembersDB()
        self._feed = feed or ProducerFeedDB()
        self._owners = owners or EventOwners()

    @staticmethod
    def gateway_url() -> str | None:
        """The same value `/.well-known/openid-configuration` publishes as `igni_gateway`."""
        return settings.fleet_gateway_url.rstrip('/') or None

    async def of(self, caller: ProducerCaller) -> CloudDiscovery:
        """What the desktop's switch is built from, for this person in the organisation they signed in to."""
        org_id = caller.org_id or ''
        group = await self._group(caller.id, org_id)
        reason = await self._unavailable(caller.id, org_id, group)
        return CloudDiscovery(
            cloud='available' if reason is None else 'unavailable',
            reason=reason,
            gateway_url=self.gateway_url(),
            mode=group.mode if group is not None else LOCAL_AND_CLOUD,
            events_owner=await self._owners.of(org_id, caller.id),
            group=CloudGroup(id=group.id, name=group.name) if group is not None else None,
            producer=f'{caller.kind}:{caller.id}',
        )

    async def set_events_owner(self, caller: ProducerCaller, owner: EventsOwner) -> CloudDiscoveryChange:
        """Make *owner* pull the person's events; `ModeForbidsOwner` when their group's mode does not allow it."""
        org_id = caller.org_id or ''
        was = await self._owners.choose(org_id, caller.id, owner)
        return CloudDiscoveryChange(discovery=await self.of(caller), owner_was=was)

    async def _group(self, user_id: str, org_id: str) -> GroupSummary | None:
        """Their group, placing them in the Default one if they are in none — as the pack does.

        A member no longer active gets no group placed; the one they were
        in, if any, is still named, so the answer says which group it was.
        None for somebody in no group at all.
        """
        summary = await self._default_group.ensure_for_user(user_id, org_id)
        return summary or await self._members.group_of(org_id, user_id)

    async def _unavailable(
        self, user_id: str, org_id: str, group: GroupSummary | None
    ) -> CloudUnavailableReason | None:
        if self.gateway_url() is None:
            return 'fleet_not_configured'
        if not self._licence.status(Module.FLEET).allows(Operation.WRITE):
            return 'not_licensed'
        member = (await self._feed.members_in(org_id, [user_id])).get(user_id)
        if member is None or not member.active:
            return 'inactive'
        if group is not None and group.mode == LOCAL:
            return 'group_local_only'
        return None
