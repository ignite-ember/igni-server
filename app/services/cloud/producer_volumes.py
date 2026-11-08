"""Growing one producer's volume past its profile's size (`docs/service-identity.md` §12.1).

An org admin, or the super admin, grows a producer's volume up to its
profile's `volume_max_gib`; it is never shrunk. The size is recorded
with who grew it (and the request is in the audit trail), and the feed
lists the producer again with its new `volume_gib`, which is how the
gateway learns to grow the volume.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.api.base_v1.schema.vm_commands import Requester
from app.api.base_v1.schema.vm_profiles import ProducerVolume
from app.db.sql.models.producer_volume import ProducerVolumeSize
from app.db.sql.producer_volumes import ProducerVolumesDB
from app.security.org_authority import OrgAuthority
from app.security.producer import ProducerRef
from app.services.auth.jwt_service import PERSON
from app.services.cloud.producer_lookup import ProducerLookup
from app.services.cloud.vm_profiles import VmProfiles
from app.services.cloud.volume_sizes import VolumeSizes

ORG_ADMIN = 'org_admin'
SUPER_ADMIN = 'super_admin'


class ProducerVolumes:
    def __init__(self, *, producers: ProducerLookup | None = None) -> None:
        self._db = ProducerVolumesDB()
        self._producers = producers or ProducerLookup()

    async def read(self, org_id: str, producer: ProducerRef) -> ProducerVolume:
        await self._producers.exists(org_id, producer)
        profile = await VmProfiles().for_producer(org_id, producer)
        incarnation = await VolumeSizes.incarnation(org_id, producer) or ''
        grown = await self._db.get(org_id, producer.kind, producer.id)
        mine = grown is not None and grown.incarnation == incarnation
        return ProducerVolume(
            org_id=org_id,
            producer=producer,
            incarnation=incarnation,
            volume_gib=profile.volume_gib,
            volume_max_gib=profile.volume_max_gib,
            grown_by=(
                Requester(kind=grown.grown_by_kind, id=grown.grown_by_id, acting_as=grown.grown_as)
                if grown is not None and mine
                else None
            ),
            grown_at=grown.grown_at if grown is not None and mine else None,
        )

    async def grow(self, authority: OrgAuthority, producer: ProducerRef, gib: int) -> ProducerVolume:
        """To *gib*: more than now (the same size is a no-op), at most the profile's maximum."""
        now = await self.read(authority.org_id, producer)
        if gib < now.volume_gib:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'The volume is {now.volume_gib} GiB; a volume is never shrunk',
            )
        if gib > now.volume_max_gib:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"This producer's profile lets its volume grow to at most {now.volume_max_gib} GiB",
            )
        if gib == now.volume_gib:
            return now
        await self._db.put(
            authority.org_id,
            ProducerVolumeSize(
                producer_kind=producer.kind,
                producer_id=producer.id,
                incarnation=now.incarnation,
                gib=gib,
                grown_by_kind=PERSON,
                grown_by_id=authority.user_id,
                grown_as=ORG_ADMIN if authority.org_role == 'admin' else SUPER_ADMIN,
            ),
        )
        return await self.read(authority.org_id, producer)
