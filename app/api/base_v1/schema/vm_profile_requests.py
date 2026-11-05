"""Pydantic schemas for VM profile requests: what an app or an extension asks its VM to get.

See `docs/service-identity.md` §12.3.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AliasGenerator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.api.base_v1.schema.effective_vm_profile import EffectiveVmProfile
from app.api.base_v1.schema.vm_commands import Requester
from app.api.base_v1.schema.vm_profiles import VmProfileShape

#: Who asked: an app, or an extension (through its manifest).
SubjectKind = Literal['app', 'extension']
RequestState = Literal['pending', 'approved', 'rejected']

#: The longest reason a rejection may give.
MAX_REASON = 500


class VmProfileRequestSettings(VmProfileShape):
    """What an app asks for: everything a profile says but its name.

    A field left out is the deployment's Standard value, as in a profile.
    A field igni does not know is refused rather than dropped: what is
    approved is what was meant.
    """

    model_config = ConfigDict(extra='forbid')


class ManifestVm(VmProfileShape):
    """An extension manifest's `vm` section: the same, spelled as the manifest spells (camelCase)."""

    model_config = ConfigDict(alias_generator=AliasGenerator(validation_alias=to_camel), extra='forbid')

    def settings(self) -> VmProfileRequestSettings:
        return VmProfileRequestSettings.model_validate(self.model_dump())


class RequestSubject(BaseModel):
    kind: SubjectKind
    id: str
    #: The app's registered name, or the extension's.
    name: str


class RequestedBy(BaseModel):
    #: `app`: the app itself; `service`: the service whose extension's manifest asks.
    kind: Literal['app', 'service']
    id: str


class VmProfileRequestRecord(BaseModel):
    """One request, as an org admin decides it and as its requester reads it."""

    id: str
    org_id: str
    subject: RequestSubject
    state: RequestState
    #: Exactly what would run: every value, the Standard ones filled in.
    requested: VmProfileRequestSettings
    #: Changes whenever anything requested does; an approval names it.
    version: str
    #: Of the packages and setup script alone: the layer Fleet would build (`pkg-<packages_version>`).
    packages_version: str
    #: False when it asks for more than the platform's caps allow; it cannot be approved then.
    within_caps: bool
    requested_by: RequestedBy
    requested_at: datetime
    decided_by: Requester | None = None
    decided_at: datetime | None = None
    #: A rejection's reason, if the admin gave one.
    reason: str | None = None
    #: The profile an approval made, holding what was last approved; null before any.
    profile_id: str | None = None


class VmProfileRequestList(BaseModel):
    requests: list[VmProfileRequestRecord]


class ApproveVmProfileRequest(BaseModel):
    #: The request's `version` as the admin read it: an approval covers exactly that.
    version: str = Field(min_length=1, max_length=64)


class RejectVmProfileRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=MAX_REASON)


class AssignedCounts(BaseModel):
    groups: int
    apps: int


class ServiceVmProfile(BaseModel):
    """What a service reads about its extension's request in one organisation."""

    org_id: str
    #: The extension this service ships; null for none.
    extension_id: str | None = None
    #: Null while the extension's manifest asks for nothing.
    request: VmProfileRequestRecord | None = None
    #: What an approval made, as a VM gets it (clamped to the caps); null before any, or once deleted.
    profile: EffectiveVmProfile | None = None
    #: How many groups and apps the organisation gave that profile.
    assigned: AssignedCounts = Field(default_factory=lambda: AssignedCounts(groups=0, apps=0))
