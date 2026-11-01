"""Pydantic schemas for an app's VM erased after inactivity (`docs/service-identity.md` §12.2)."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.api.base_v1.schema.retention import MAX_SUMMARY
from app.security.producer import ProducerRef

#: Why a VM's state and volume went: it was not woken for its profile's
#: `erase_after_idle_days`. The only reason so far.
ResetReason = Literal['idle']


class VmResetBody(BaseModel):
    """What the gateway asks (`check`) or reports (an erasure after inactivity done)."""

    #: The producer's life whose VM it is (§11.3); it must be the current one.
    incarnation: str = Field(min_length=1, max_length=200)
    reason: ResetReason = 'idle'
    #: When the VM was last woken, as the gateway saw it.
    last_woken_at: datetime | None = None
    #: What was erased, part by part (`state`, `volume`, `keys`, …), as the node answered.
    summary: dict[str, Any] = Field(default_factory=dict)

    @field_validator('summary')
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > MAX_SUMMARY:
            raise ValueError(f'summary is limited to {MAX_SUMMARY} characters of JSON')
        return value


class VmResetResponse(BaseModel):
    org_id: str
    producer: ProducerRef
    incarnation: str
    reason: ResetReason
    #: What the next fresh VM authenticates with: `reissued` (a new
    #: credential waits for the node agent's pickup), `awaiting_pickup`
    #: (one was waiting already), `none` (no working credential: the
    #: cloud is not enabled for the app).
    credential: Literal['reissued', 'awaiting_pickup', 'none']
    reset_at: datetime
