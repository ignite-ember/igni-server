"""The VM profile a producer's VM gets, as served: what the producers feed carries.

Apart from `app.api.base_v1.schema.vm_profiles` because the feed's own
store holds it, and so it depends on nothing but pydantic. See
`docs/service-identity.md` §11.3 and §12.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

#: One package as a package manager names it — `chromium`, `requests==2.32.3`,
#: `@playwright/test@1.49` — and nothing a shell would read as more than one word.
PackageName = Annotated[
    str, StringConstraints(pattern=r'^[A-Za-z0-9@][A-Za-z0-9@/._+:=<>~^*!,-]{0,199}$', strip_whitespace=True)
]

#: At most this many packages per manager.
MAX_PACKAGES = 200


class VmPackages(BaseModel):
    """What the profile's read-only layer is built with, per package manager."""

    apt: list[PackageName] = Field(default_factory=list, max_length=MAX_PACKAGES)
    pip: list[PackageName] = Field(default_factory=list, max_length=MAX_PACKAGES)
    npm: list[PackageName] = Field(default_factory=list, max_length=MAX_PACKAGES)


class EffectiveVmProfile(BaseModel):
    """What a producer's VM gets from its profile: its values clamped to the platform's caps.

    The gateway reads it from the producers feed (§11.3), so a wake never
    asks igni server for it.
    """

    profile_id: str
    profile_name: str
    #: The VM's size at boot: the most it may have.
    memory_mib: int
    #: What it normally has; the node's balloon holds the rest back.
    normal_memory_mib: int
    #: For placement; null: the node's rule.
    expected_memory_mib: int | None
    vcpus: int
    idle_before_pause_s: int | None
    packages: VmPackages
    setup_script: str | None
    #: How far the producer's volume may be grown, in GiB.
    volume_max_gib: int
    #: Days without a wake after which an app's or a space's VM state
    #: and volume are erased; null is never. Not applied to a person's
    #: VM (§12.2). Absent on a row written before profiles had it: never.
    erase_after_idle_days: int | None = None
    #: Whether the VM may reach the internet; false: only what its node
    #: allows. The node grants it only where it may (§12.4). Absent on a
    #: row written before profiles had it: false.
    internet: bool = False
