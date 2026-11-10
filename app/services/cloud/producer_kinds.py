"""What the producers of each kind — people, apps, spaces — are now, read a whole set at a time."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from app.db.sql.models.app_space import SPACE
from app.db.sql.models.producer_feed import PERSON
from app.db.sql.models.registered_client import APP
from app.db.sql.producer_feed import ProducerFeedDB
from app.db.sql.producer_feed_tenant import ProducerFeedTenantDB
from app.db.sql.producer_volumes import ProducerVolumesDB
from app.db.sql.retention import RetentionDB
from app.db.sql.schemas.producer_feed import Census, ProducerKey
from app.db.sql.schemas.tenancy import OrganisationDetached
from app.db.sql.spaces import SpacesDB
from app.services.cloud.producer_profiles import ProducerProfiles
from app.services.cloud.retention_rules import Retention


class KindStates(ABC):
    """The producers of one kind in an organisation."""

    kind: ClassVar[str]

    @abstractmethod
    async def current(self, org_id: str) -> list[str]:
        """Every producer of this kind the organisation has now, active or not."""

    @abstractmethod
    async def of(self, org_id: str, *, ids: list[str], profiles: ProducerProfiles) -> Census:
        """What each of *ids* is now: there (active or not), removed by its owner, or neither."""

    def _key(self, org_id: str, producer_id: str) -> ProducerKey:
        return ProducerKey(org_id=org_id, producer_kind=self.kind, producer_id=producer_id)


class PeopleStates(KindStates):
    """A member, active or deactivated, is there; anybody else asked about was removed from the org.

    A person deactivated is not removed; their VM is only suspended. A person
    in no group yet gets the Default group's profile, as the VM profile
    endpoint does (it puts them there).
    """

    kind = PERSON

    def __init__(
        self,
        *,
        feed: ProducerFeedDB | None = None,
        tenant: ProducerFeedTenantDB | None = None,
        volumes: ProducerVolumesDB | None = None,
        holds: RetentionDB | None = None,
        retention: Retention | None = None,
    ) -> None:
        self._feed = feed or ProducerFeedDB()
        self._tenant = tenant or ProducerFeedTenantDB()
        self._volumes = volumes or ProducerVolumesDB()
        self._holds = holds or RetentionDB()
        self._retention = retention or Retention()

    async def current(self, org_id: str) -> list[str]:
        return list(await self._feed.members_in(org_id, None))

    async def of(self, org_id: str, *, ids: list[str], profiles: ProducerProfiles) -> Census:
        members = await self._feed.members_in(org_id, ids)
        groups = await self._tenant.groups_of(org_id, list(members))
        grown = await self._volumes.of(org_id, PERSON, list(members))
        # A legal hold stops their VM's erasure after inactivity too (§12.3).
        held_now = await self._holds.held_in(org_id, sorted(members)) if members else set()
        states = [
            profiles.state(
                self._key(org_id, user_id),
                profile_id=profiles.of_group(groups.get(user_id)),
                active=member.active,
                incarnation=member.incarnation,
                grown=grown.get(user_id),
                legal_hold=user_id in held_now,
            )
            for user_id, member in members.items()
        ]
        removed = {user_id for user_id in ids if user_id not in members}
        # The organisation keeps a removed member's VM for its retention;
        # an account deleted, or the organisation, goes at once.
        days = await self._retention.removed_member_days(org_id)
        gone = await self._gone(org_id, removed) if removed else set()
        kept = removed - gone
        return Census(
            states=states,
            removed=removed,
            erase_after_days={user_id: 0 if user_id in gone else days for user_id in ids},
            held=await self._holds.held_in(org_id, sorted(kept)) if kept else set(),
        )

    async def _gone(self, org_id: str, removed: set[str]) -> set[str]:
        """Those of *removed* whose account, or whose organisation, was deleted."""
        if not await self._feed.org_exists(org_id):
            return removed
        return removed - await self._feed.accounts_in(sorted(removed))


class AppStates(KindStates):
    """An app is there while registered; it is active once the cloud is enabled for it."""

    kind = APP

    def __init__(
        self,
        *,
        feed: ProducerFeedDB | None = None,
        tenant: ProducerFeedTenantDB | None = None,
        volumes: ProducerVolumesDB | None = None,
        holds: RetentionDB | None = None,
    ) -> None:
        self._feed = feed or ProducerFeedDB()
        self._tenant = tenant or ProducerFeedTenantDB()
        self._volumes = volumes or ProducerVolumesDB()
        self._holds = holds or RetentionDB()

    async def current(self, org_id: str) -> list[str]:
        return await self._feed.apps_in(org_id, None)

    async def of(self, org_id: str, *, ids: list[str], profiles: ProducerProfiles) -> Census:
        registered = await self._feed.apps_in(org_id, ids)
        held = await self._tenant.apps(org_id, registered)
        cloud = await self._tenant.with_credential(org_id, APP, list(held))
        grown = await self._volumes.of(org_id, APP, list(held))
        # A legal hold on an app keeps it from erasure — after inactivity, or once discarded (§18.2).
        on_hold = await self._holds.held_apps(org_id)
        removed = {app_id for app_id in ids if app_id not in held}
        return Census(
            states=[
                profiles.state(
                    self._key(org_id, app_id),
                    profile_id=profile,
                    active=app_id in cloud,
                    grown=grown.get(app_id),
                    legal_hold=app_id in on_hold,
                )
                for app_id, profile in held.items()
            ],
            removed=removed,
            held=removed & on_hold,
            held_apps=on_hold,
        )


class SpaceStates(KindStates):
    """A space is there while declared and its app registered; it has its app's profile."""

    kind = SPACE

    def __init__(
        self,
        *,
        feed: ProducerFeedDB | None = None,
        tenant: ProducerFeedTenantDB | None = None,
        spaces: SpacesDB | None = None,
        volumes: ProducerVolumesDB | None = None,
        holds: RetentionDB | None = None,
    ) -> None:
        self._feed = feed or ProducerFeedDB()
        self._tenant = tenant or ProducerFeedTenantDB()
        self._spaces = spaces or SpacesDB()
        self._volumes = volumes or ProducerVolumesDB()
        self._holds = holds or RetentionDB()

    async def current(self, org_id: str) -> list[str]:
        try:
            return list(await self._spaces.apps_of(org_id, None))
        except OrganisationDetached:
            return []

    async def of_app(self, org_id: str, app_id: str) -> list[str]:
        """Every space of one app."""
        try:
            return await self._spaces.of_app(org_id, app_id)
        except OrganisationDetached:
            return []

    async def of(self, org_id: str, *, ids: list[str], profiles: ProducerProfiles) -> Census:
        apps_of = await self._spaces.apps_of(org_id, ids)
        owners = sorted(set(apps_of.values()))
        registered = set(await self._feed.apps_in(org_id, owners))
        held = await self._tenant.apps(org_id, sorted(registered))
        grown = await self._volumes.of(org_id, SPACE, list(apps_of))
        # A hold on the app covers its spaces.
        on_hold = await self._holds.held_apps(org_id)
        states = [
            profiles.state(
                self._key(org_id, space_id),
                profile_id=held[app_id],
                active=True,
                owner=app_id,
                grown=grown.get(space_id),
                legal_hold=app_id in on_hold,
            )
            for space_id, app_id in apps_of.items()
            if app_id in held
        ]
        there = {state.key.producer_id for state in states}
        return Census(states=states, removed={space_id for space_id in ids if space_id not in there}, held_apps=on_hold)
