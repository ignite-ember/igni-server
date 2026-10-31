"""What the deployment's own checks say, for the people who run it.

* `GET /portal/deployment/notices` — a super admin: the warnings the
  startup checks write to the log (`app/security/startup_checks.py`),
  one sentence each, empty when there are none. The portal shows them
  as a banner.

Super admins only: the notices describe how the deployment is
configured, which is theirs to change and nobody else's business.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.base_v1.schema.portal.user import PortalAccount
from app.security.startup_checks import deployment_notices
from app.security.super_admin import require_super_admin

router = APIRouter()


class DeploymentNotices(BaseModel):
    notices: list[str]


@router.get('/deployment/notices', response_model=DeploymentNotices)
async def notices(_account: PortalAccount = Depends(require_super_admin)) -> DeploymentNotices:
    return DeploymentNotices(notices=deployment_notices())
