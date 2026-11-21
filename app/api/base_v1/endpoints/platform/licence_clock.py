"""The deployment's single licence high-water mark, for the modules and the gateway.

igni server holds the one mark (the latest time the deployment has seen)
in its encrypted integrity file. The paid modules and the Fleet gateway no
longer keep their own; they read this value over their existing platform
link and cache it, so each component evaluates the licence at the same
`max(now, mark − 24h, issued)` and a clock wound back on one host does not
revive a lapsed licence. Requires a platform token (`require_platform`).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.security.platform_caller import PlatformCaller, require_platform
from app.services.deployment_licence import deployment_licence, DeploymentLicence

router = APIRouter()


class CentralMark(BaseModel):
    """The deployment's high-water mark. `seen` is None before the first time is recorded."""

    seen: datetime | None = None


@router.get('/licence/clock', response_model=CentralMark)
async def central_mark(
    _platform: PlatformCaller = Depends(require_platform),
    licence: DeploymentLicence = Depends(deployment_licence),
) -> CentralMark:
    return CentralMark(seen=licence.high_water.seen)
