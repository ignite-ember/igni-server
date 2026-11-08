"""What each kind of job scope — a group, a profile, an organisation, the platform — checks and reaches."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from fastapi import HTTPException, status

from app.api.base_v1.schema.vm_commands import JobScope, ScopeKind
from app.db.sql.groups import GroupsDB
from app.db.sql.models.vm_command import VmJob
from app.db.sql.vm_profiles import VmProfilesDB
from app.services.cloud.producer_lookup import VmPlacement


class ScopeRule(ABC):
    """One kind of job scope."""

    kind: ClassVar[ScopeKind]

    @abstractmethod
    async def check(self, org_id: str, scope_id: str | None) -> None:
        """Refuse a scope the organisation has no such thing for."""

    @abstractmethod
    def reaches(self, scope_id: str | None, vm: VmPlacement) -> bool:
        """Whether a job of this scope reaches a VM placed so."""


class GroupScope(ScopeRule):
    kind = 'group'

    def __init__(self, *, groups: GroupsDB | None = None) -> None:
        self._groups = groups or GroupsDB()

    async def check(self, org_id: str, scope_id: str | None) -> None:
        if not scope_id or await self._groups.get_group(org_id, group_id=scope_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Group not found')

    def reaches(self, scope_id: str | None, vm: VmPlacement) -> bool:
        return vm.group_id is not None and scope_id == vm.group_id


class ProfileScope(ScopeRule):
    kind = 'profile'

    def __init__(self, *, profiles: VmProfilesDB | None = None) -> None:
        self._profiles = profiles or VmProfilesDB()

    async def check(self, org_id: str, scope_id: str | None) -> None:
        if not scope_id or await self._profiles.get(org_id, scope_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='VM profile not found')

    def reaches(self, scope_id: str | None, vm: VmPlacement) -> bool:
        return scope_id == vm.profile_id


class EveryVmScope(ScopeRule):
    """A scope of every VM there is in it, which names no id."""

    async def check(self, org_id: str, scope_id: str | None) -> None:
        if scope_id is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'A {self.kind} job names no id')

    def reaches(self, scope_id: str | None, vm: VmPlacement) -> bool:
        return True


class OrgScope(EveryVmScope):
    kind = 'org'


class PlatformScope(EveryVmScope):
    kind = 'platform'


class JobScopes:
    """Every kind of job scope, by its kind."""

    def __init__(self, *, rules: list[ScopeRule] | None = None) -> None:
        self._rules = {rule.kind: rule for rule in rules or [GroupScope(), ProfileScope(), OrgScope(), PlatformScope()]}

    async def check(self, org_id: str, scope: JobScope) -> None:
        await self._rules[scope.kind].check(org_id, scope.id)

    def reaches(self, job: VmJob, vm: VmPlacement) -> bool:
        scope = JobScope.stored(job.scope_kind, job.scope_id)
        return self._rules[scope.kind].reaches(scope.id, vm)
