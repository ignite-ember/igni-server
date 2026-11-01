"""An app's VM erased after inactivity: its memory and volume gone, the app itself kept.

A profile's `erase_after_idle_days` has the gateway erase the state and
volume of an app's or a space's VM not woken for that long
(`docs/service-identity.md` §12.2). It asks first (`check`): a person's
VM — never erased this way while they are a member —, a setting turned
off, a legal hold on the app, or a life that is no longer current refuses
it. Then it reports the erasure here: one row in the organisation's audit
trail, and, since the VM's credential lived on the volume (§16), a new
one issued for the node agent's pickup at the next fresh start.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.api.base_v1.schema.vm_resets import VmResetBody, VmResetResponse
from app.db.sql.audit_events import AuditEventDB, AuditRecord
from app.db.sql.models.app_space import SPACE
from app.db.sql.retention import RetentionDB
from app.db.sql.spaces import SpacesDB
from app.db.sql.vm_credentials import VmCredentialsDB
from app.security.platform_caller import PlatformCaller
from app.security.producer import ProducerRef
from app.services.auth.jwt_service import PERSON
from app.services.cloud.vm_credentials import VmCredentials
from app.services.cloud.vm_profiles import VmProfiles
from app.services.cloud.volume_sizes import VolumeSizes

#: How these rows read in the trail: not a person's request, the platform's act.
ROUTE = 'producer-vm/reset'


class VmResets:
    @staticmethod
    def _conflict(detail: str) -> HTTPException:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    async def check(self, org_id: str, producer: ProducerRef, body: VmResetBody) -> int:
        """Refuse what may not be erased now; the profile's `erase_after_idle_days` when it may."""
        if producer.kind == PERSON:
            raise self._conflict("A member's VM is never erased for inactivity: it is kept while they are a member")
        if await VolumeSizes.incarnation(org_id, producer) != body.incarnation:
            raise self._conflict(f'{body.incarnation} is not the current life of this {producer.kind}')
        profile = await VmProfiles().for_producer(org_id, producer)
        if profile.erase_after_idle_days is None:
            raise self._conflict("This app's VM profile does not erase its VM after inactivity")
        app_id = producer.id
        if producer.kind == SPACE:
            space = await SpacesDB().get(org_id, producer.id)
            app_id = space.app_id if space is not None else app_id
        hold = await RetentionDB().app_hold(org_id, app_id)
        if hold is not None:
            raise self._conflict(
                f'A legal hold stands on this app (placed {hold.placed_at:%Y-%m-%d}: {hold.reason}); '
                'nothing of its VM is erased until it is released'
            )
        return profile.erase_after_idle_days

    async def record(
        self, org_id: str, producer: ProducerRef, body: VmResetBody, *, by: PlatformCaller
    ) -> VmResetResponse:
        """Audit the erasure and make sure the next fresh VM can authenticate."""
        days = await self.check(org_id, producer, body)
        credential = await self._credential(org_id, producer, by_client_id=by.client_id)
        await AuditEventDB().record(
            AuditRecord(
                method='SYSTEM',
                route=ROUTE,
                status_code=200,
                org_id=org_id,
                target_type=f'{producer.kind}_vm',
                target_id=producer.id,
                detail={
                    'incarnation': body.incarnation,
                    'why': 'erased_after_idle',
                    'erase_after_idle_days': days,
                    'last_woken_at': body.last_woken_at.isoformat() if body.last_woken_at else None,
                    'credential': credential,
                    'by_client': by.client_id,
                    'summary': body.summary,
                },
            )
        )
        return VmResetResponse(
            org_id=org_id,
            producer=producer,
            incarnation=body.incarnation,
            reason=body.reason,
            credential=credential,  # type: ignore[arg-type]
            reset_at=datetime.now(timezone.utc),
        )

    @staticmethod
    async def _credential(org_id: str, producer: ProducerRef, *, by_client_id: str) -> str:
        """The VM's credential went with its volume: a new one waits for the node agent's pickup."""
        working = await VmCredentialsDB().active(org_id, producer_kind=producer.kind, producer_id=producer.id)
        if not working:
            return 'none'
        if any(row.pending_pickup is not None for row in working):
            return 'awaiting_pickup'
        await VmCredentials().issue(org_id, producer, by_kind='platform', by_id=by_client_id, for_pickup=True)
        return 'reissued'
