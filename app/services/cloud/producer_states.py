"""What a set of one organisation's producers are now, as the feed reports them.

Read with a few queries for the whole set rather than one call per
producer, so a mark naming a hundred thousand producers costs a handful
of statements per kind (`docs/service-identity.md` §11.3).
"""

from __future__ import annotations

from app.api.base_v1.schema.vm_profiles import PlatformVmCapsBody
from app.db.sql.schemas.producer_feed import Census
from app.db.sql.schemas.tenancy import OrganisationDetached
from app.db.sql.vm_profiles import VmProfilesDB
from app.services.cloud.producer_kinds import AppStates, KindStates, PeopleStates, SpaceStates
from app.services.cloud.producer_profiles import ProducerProfiles
from app.services.cloud.vm_profiles import VmProfiles


class ProducerStates:
    """One organisation's producers of every kind, its profiles read once for all of them."""

    def __init__(
        self,
        org_id: str,
        *,
        caps: PlatformVmCapsBody,
        people: PeopleStates | None = None,
        apps: AppStates | None = None,
        spaces: SpaceStates | None = None,
        vm_profiles: VmProfiles | None = None,
        profiles_db: VmProfilesDB | None = None,
    ) -> None:
        self._org_id = org_id
        self._caps = caps
        self._spaces = spaces or SpaceStates()
        kinds: list[KindStates] = [people or PeopleStates(), apps or AppStates(), self._spaces]
        self._kinds = {states.kind: states for states in kinds}
        self._vm_profiles = vm_profiles or VmProfiles()
        self._profiles_db = profiles_db or VmProfilesDB()
        self._profiles: ProducerProfiles | None = None

    async def current(self, kind: str) -> list[str]:
        """Every producer of this kind the organisation has now, active or not."""
        states = self._kinds.get(kind)
        return await states.current(self._org_id) if states is not None else []

    async def spaces_of(self, app_id: str) -> list[str]:
        """Every space of one app."""
        return await self._spaces.of_app(self._org_id, app_id)

    async def of(self, kind: str, ids: list[str]) -> Census:
        """What each of these is now: there (active or not), removed by its owner, or neither.

        Removed: a space deleted, an app deleted, a person removed from the
        organisation or their account deleted, the organisation deleted.
        """
        states = self._kinds.get(kind)
        if not ids or states is None:
            return Census()
        try:
            return await states.of(self._org_id, ids=ids, profiles=await self._loaded())
        except OrganisationDetached:
            # Deleted: everything it had was removed with it.
            return Census(removed=set(ids))

    async def _loaded(self) -> ProducerProfiles:
        if self._profiles is None:
            self._profiles = await ProducerProfiles.load(
                self._org_id, caps=self._caps, vm_profiles=self._vm_profiles, db=self._profiles_db
            )
        return self._profiles
