"""How big a producer's volume is: its profile's size, or more where an admin grew it.

Read by the VM profile a producer gets and by the feed (`docs/service-identity.md`
§12.1). A size grown for one incarnation of a producer (§11.3) does not
carry over to the next: a person added again starts at the profile's.
"""

from __future__ import annotations

from app.api.base_v1.schema.vm_profiles import VmProfileSettings
from app.db.sql.models.app_space import SPACE
from app.db.sql.models.producer_volume import ProducerVolumeSize
from app.db.sql.models.registered_client import APP
from app.db.sql.producer_volumes import ProducerVolumesDB
from app.db.sql.team import TeamDB
from app.security.producer import ProducerRef


class VolumeSizes:
    @staticmethod
    def of(effective: VmProfileSettings, grown: ProducerVolumeSize | None, incarnation: str) -> int:
        """The profile's size, or the grown one if larger and for this incarnation. Never less than grown."""
        if grown is None or grown.incarnation != incarnation:
            return effective.volume_gib
        return max(effective.volume_gib, grown.gib)

    @staticmethod
    async def incarnation(org_id: str, producer: ProducerRef) -> str | None:
        """Which life of the producer this is: a person's membership id; an app's or a space's own id."""
        if producer.kind in (APP, SPACE):
            return producer.id
        member = await TeamDB().get_member(org_id, producer.id)
        return str(member.id) if member is not None and member.status != 'invited' else None

    @classmethod
    async def effective(cls, org_id: str, producer: ProducerRef, effective: VmProfileSettings) -> int:
        incarnation = await cls.incarnation(org_id, producer)
        grown = await ProducerVolumesDB().get(org_id, producer.kind, producer.id)
        return cls.of(effective, grown, incarnation or '')
