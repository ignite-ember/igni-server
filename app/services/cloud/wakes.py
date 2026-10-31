"""Which producers' VMs the gateway should wake now, and why.

A VM is woken for a command meant for it alone, and for an event
waiting for it (`docs/service-identity.md` §13, §15). A job for many
VMs never wakes anybody: each VM gets it at its next wake.
"""

from __future__ import annotations

from app.api.base_v1.schema.vm_commands import WakeReason, Wakes
from app.services.cloud.producer_events import ProducerEvents
from app.services.cloud.vm_commands import VmCommands


class WakeList:
    async def now(self) -> Wakes:
        merged: dict[tuple[str, str, str], WakeReason] = {}
        for reason in [*await VmCommands().wakes(), *await ProducerEvents().wakes()]:
            found = (reason.org_id, reason.producer.kind, reason.producer.id)
            if found in merged:
                merged[found].reasons.extend(r for r in reason.reasons if r not in merged[found].reasons)
            else:
                merged[found] = reason
        return Wakes(producers=list(merged.values()))
