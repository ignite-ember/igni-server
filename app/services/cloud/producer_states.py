"""What a set of one organisation's producers are now, as the feed reports them.

Read with a few queries for the whole set rather than one call per
producer, so a mark naming a hundred thousand producers costs a handful
of statements per kind (`docs/service-identity.md` §11.3).
"""

from __future__ import annotations

from app.api.base_v1.schema.vm_profiles import PlatformVmCapsBody
from app.db.sql.models.app_space import SPACE
from app.db.sql.models.producer_feed import PERSON
from app.db.sql.models.producer_volume import ProducerVolumeSize
from app.db.sql.models.registered_client import APP
from app.db.sql.models.vm_profile import VmProfile
from app.db.sql.producer_feed import ProducerFeedDB
from app.db.sql.producer_feed_tenant import ProducerFeedTenantDB
from app.db.sql.producer_volumes import ProducerVolumesDB
from app.db.sql.retention import RetentionDB
from app.db.sql.schemas.producer_feed import Census, ProducerKey, ProducerState
from app.db.sql.schemas.tenancy import OrganisationDetached
from app.db.sql.spaces import SpacesDB
from app.db.sql.vm_profiles import VmProfilesDB
from app.services.cloud.retention_rules import Retention
from app.services.cloud.vm_profiles import ProfileVersion, VmCaps, VmProfiles
from app.services.cloud.volume_sizes import VolumeSizes


class ProducerStates:
    """One organisation's profiles, read once, applied to any of its producers."""

    def __init__(self, org_id: str, caps: PlatformVmCapsBody) -> None:
        self._org_id = org_id
        self._caps = caps
        self._feed = ProducerFeedDB()
        self._tenant = ProducerFeedTenantDB()
        self._spaces = SpacesDB()
        self._volumes = ProducerVolumesDB()
        self._profiles: dict[str, VmProfile] = {}
        self._default: VmProfile | None = None
        self._group_profiles: dict[str, str | None] = {}
        self._default_group_profile: str | None = None

    async def _load(self) -> None:
        profiles = VmProfilesDB()
        self._default = await VmProfiles().default(self._org_id)
        self._profiles = {profile.id: profile for profile in await profiles.all(self._org_id)}
        for group in await profiles.groups(self._org_id):
            self._group_profiles[group.id] = group.vm_profile_id
            if group.is_default:
                self._default_group_profile = group.vm_profile_id

    def _state(
        self,
        kind: str,
        producer_id: str,
        profile_id: str | None,
        *,
        active: bool,
        owner: str | None = None,
        incarnation: str | None = None,
        grown: ProducerVolumeSize | None = None,
        legal_hold: bool = False,
    ) -> ProducerState:
        assert self._default is not None
        profile = self._profiles.get(profile_id or '') or self._default
        effective = VmCaps.clamped(VmProfiles.settings_of(profile), self._caps)
        return ProducerState(
            key=ProducerKey(org_id=self._org_id, producer_kind=kind, producer_id=producer_id),
            active=active,
            profile_version=ProfileVersion.of(profile.id, effective),
            packages_version=ProfileVersion.packages(effective),
            profile=VmProfiles.served(profile, effective),
            owner_app_id=owner,
            incarnation=incarnation or producer_id,
            volume_gib=VolumeSizes.of(effective, grown, incarnation or producer_id),
            legal_hold=legal_hold,
        )

    async def current(self, kind: str) -> list[str]:
        """Every producer of this kind the organisation has now, active or not."""
        if kind == PERSON:
            return list(await self._feed.members_in(self._org_id, None))
        if kind == APP:
            return await self._feed.apps_in(self._org_id, None)
        if kind == SPACE:
            try:
                return list(await self._spaces.apps_of(self._org_id, None))
            except OrganisationDetached:
                return []
        return []

    async def spaces_of(self, app_id: str) -> list[str]:
        """Every space of one app."""
        try:
            return await self._spaces.of_app(self._org_id, app_id)
        except OrganisationDetached:
            return []

    async def of(self, kind: str, ids: list[str]) -> Census:
        """What each of these is now: there (active or not), removed by its owner, or neither.

        Removed: a space deleted, an app deleted, a person removed from the
        organisation or their account deleted, the organisation deleted.
        A person deactivated is not removed; their VM is only suspended. A person
        in no group yet gets the Default group's profile, as the VM
        profile endpoint does (it puts them there). An app is active once
        the cloud is enabled for it.
        """
        if not ids:
            return Census()
        try:
            if self._default is None:
                await self._load()
            if kind == PERSON:
                return await self._people(ids)
            if kind == APP:
                return await self._apps(ids)
            if kind == SPACE:
                return await self._spaces_states(ids)
        except OrganisationDetached:
            # Deleted: everything it had was removed with it.
            return Census(removed=set(ids))
        return Census()

    async def _people(self, ids: list[str]) -> Census:
        """A member, active or deactivated, is there; anybody else in the list was removed from the org."""
        members = await self._feed.members_in(self._org_id, ids)
        groups = await self._tenant.groups_of(self._org_id, list(members))
        grown = await self._volumes.of(self._org_id, PERSON, list(members))
        # A legal hold stops their VM's erasure after inactivity too (§12.3).
        held_now = await RetentionDB().held_in(self._org_id, sorted(members)) if members else set()
        states = []
        for user_id, member in members.items():
            group = groups.get(user_id)
            profile_id = self._group_profiles.get(group) if group is not None else self._default_group_profile
            states.append(
                self._state(
                    PERSON,
                    user_id,
                    profile_id,
                    active=member.active,
                    incarnation=member.incarnation,
                    grown=grown.get(user_id),
                    legal_hold=user_id in held_now,
                )
            )
        removed = {user_id for user_id in ids if user_id not in members}
        # The organisation keeps a removed member's VM for its retention;
        # an account deleted, or the organisation, goes at once.
        days = await Retention().removed_member_days(self._org_id)
        gone = set()
        if removed:
            gone = (
                removed
                if not await self._feed.org_exists(self._org_id)
                else (removed - await self._feed.accounts_in(sorted(removed)))
            )
        return Census(
            states=states,
            removed=removed,
            erase_after_days={user_id: 0 if user_id in gone else days for user_id in ids},
            held=await RetentionDB().held_in(self._org_id, sorted(removed - gone)) if removed - gone else set(),
        )

    async def _apps(self, ids: list[str]) -> Census:
        registered = await self._feed.apps_in(self._org_id, ids)
        held = await self._tenant.apps(self._org_id, registered)
        cloud = await self._tenant.with_credential(self._org_id, APP, list(held))
        grown = await self._volumes.of(self._org_id, APP, list(held))
        # A legal hold on an app keeps it from erasure — after inactivity, or once discarded (§18.2).
        on_hold = await RetentionDB().held_apps(self._org_id)
        removed = {app_id for app_id in ids if app_id not in held}
        return Census(
            states=[
                self._state(
                    APP,
                    app_id,
                    profile,
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

    async def _spaces_states(self, ids: list[str]) -> Census:
        """A space is there while declared and its app registered; it has its app's profile."""
        apps_of = await self._spaces.apps_of(self._org_id, ids)
        owners = sorted(set(apps_of.values()))
        registered = set(await self._feed.apps_in(self._org_id, owners))
        held = await self._tenant.apps(self._org_id, sorted(registered))
        grown = await self._volumes.of(self._org_id, SPACE, list(apps_of))
        # A hold on the app covers its spaces.
        on_hold = await RetentionDB().held_apps(self._org_id)
        states = [
            self._state(
                SPACE,
                space_id,
                held[app_id],
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
