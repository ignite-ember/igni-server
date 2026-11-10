"""Content hashes of VM profiles: the gateway re-provisions a VM when its version changes."""

from __future__ import annotations

import hashlib
import json

from app.api.base_v1.schema.vm_profiles import VmProfileSettings, VmProfileShape


class ProfileVersion:
    """Content hashes: equal exactly when what they cover is equal."""

    @staticmethod
    def digest(value: object) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:32]

    #: Fields added after versions were first handed out, and their values
    #: then: left out of the digest while they have them, so adding a field
    #: does not change every producer's version (which the gateway would
    #: answer by re-provisioning every VM). The version is a stable contract
    #: with the gateway, so this is a deliberate exception: dropping it is a
    #: fleet-wide re-provision and needs its own rollout.
    LATER_FIELDS: dict[str, object] = {'erase_after_idle_days': None, 'internet': False}

    @classmethod
    def of(cls, profile_id: str, effective: VmProfileSettings) -> str:
        values = effective.model_dump(mode='json')
        for field, then in cls.LATER_FIELDS.items():
            if values.get(field) == then:
                values.pop(field)
        return cls.digest({'id': profile_id, **values})

    @classmethod
    def packages(cls, effective: VmProfileShape) -> str:
        return cls.digest({'packages': effective.packages.model_dump(), 'setup_script': effective.setup_script})
