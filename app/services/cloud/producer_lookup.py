"""Whether a producer is in an organisation, and where its VM stands for jobs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from fastapi import HTTPException, status
from pydantic import BaseModel

from app.db.sql.apps import AppsDB
from app.db.sql.models.app_space import SPACE
from app.db.sql.models.producer_feed import PERSON
from app.db.sql.models.registered_client import APP
from app.db.sql.organizations import OrganizationDB
from app.db.sql.spaces import SpacesDB
from app.db.sql.team import TeamDB
from app.db.sql.vm_profiles import VmProfilesDB
from app.security.producer import ProducerRef
from app.services.cloud.vm_profiles import VmProfiles
from app.services.portal.group_extensions import GroupExtensions


class VmPlacement(BaseModel):
    """What decides which jobs reach a producer's VM."""

    #: A person's group; None for an app, a space, or a person in none.
    group_id: str | None
    #: The producer's effective VM profile.
    profile_id: str


class KindLookup(ABC):
    """The producers of one kind."""

    kind: ClassVar[str]

    @abstractmethod
    async def found(self, org_id: str, producer_id: str) -> bool:
        """Whether the organisation has this producer."""

    async def group_of(self, org_id: str, producer_id: str) -> str | None:
        """The producer's group; only a person has one."""
        return None


class PersonLookup(KindLookup):
    kind = PERSON

    def __init__(self, *, team: TeamDB | None = None, groups: GroupExtensions | None = None) -> None:
        self._team = team or TeamDB()
        self._groups = groups or GroupExtensions()

    async def found(self, org_id: str, producer_id: str) -> bool:
        return await self._team.get_active_member(org_id, producer_id) is not None

    async def group_of(self, org_id: str, producer_id: str) -> str | None:
        return await self._groups.group_of(producer_id, org_id)


class AppLookup(KindLookup):
    kind = APP

    def __init__(self, *, apps: AppsDB | None = None) -> None:
        self._apps = apps or AppsDB()

    async def found(self, org_id: str, producer_id: str) -> bool:
        return await self._apps.get(org_id, producer_id) is not None


class SpaceLookup(KindLookup):
    kind = SPACE

    def __init__(self, *, spaces: SpacesDB | None = None) -> None:
        self._spaces = spaces or SpacesDB()

    async def found(self, org_id: str, producer_id: str) -> bool:
        return await self._spaces.get(org_id, producer_id) is not None


class ProducerLookup:
    """What a producer is, for deciding whether it exists and which jobs reach its VM."""

    def __init__(
        self,
        *,
        kinds: list[KindLookup] | None = None,
        organisations: OrganizationDB | None = None,
        profiles: VmProfiles | None = None,
        profile_rows: VmProfilesDB | None = None,
    ) -> None:
        self._kinds = {lookup.kind: lookup for lookup in kinds or [PersonLookup(), AppLookup(), SpaceLookup()]}
        self._organisations = organisations or OrganizationDB()
        self._profiles = profiles or VmProfiles()
        self._profile_rows = profile_rows or VmProfilesDB()

    async def exists(self, org_id: str, producer: ProducerRef) -> None:
        """404 unless the organisation exists and has this producer."""
        if await self._organisations.get_by_id(org_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Organisation not found')
        if not await self._kinds[producer.kind].found(org_id, producer.id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such producer in this organisation')

    async def placement(self, org_id: str, producer: ProducerRef) -> VmPlacement:
        """The producer's group and its effective profile — its assigned one while that still exists."""
        group_id = await self._kinds[producer.kind].group_of(org_id, producer.id)
        assigned = await self._profiles.assigned(org_id, producer)
        if assigned is None or await self._profile_rows.get(org_id, assigned) is None:
            assigned = (await self._profiles.default(org_id)).id
        return VmPlacement(group_id=group_id, profile_id=assigned)
