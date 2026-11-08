"""The super admin's priority order of extensions, and setting it.

Two extensions that want one thing collide, and the higher one in this
order has it; several extensions' prompt edits stack in it and their
hooks are asked in it. Set once here, it reaches every machine
with the distribution listing (`app.services.identity.priority`).

Deployment-wide, so `require_deployment_admin`. Each change is recorded
by the audit middleware, with the order set.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.base_v1.schema.portal.user import PortalAccount
from app.middlewares.request_audit import RequestAudit
from app.security.deployment_admin import require_deployment_admin
from app.services.identity.priority import ExtensionPriority, PriorityRefused, PriorityView

router = APIRouter()


class NewPriority(BaseModel):
    #: Every extension there is, highest first.
    order: list[str] = Field(max_length=500)


@router.get('/extension-priority', response_model=PriorityView)
async def priority(_admin: PortalAccount | None = Depends(require_deployment_admin)) -> PriorityView:
    return await ExtensionPriority().view()


@router.put('/extension-priority', response_model=PriorityView)
async def set_priority(
    request: Request, new: NewPriority, admin: PortalAccount | None = Depends(require_deployment_admin)
) -> PriorityView:
    ordering = ExtensionPriority()
    try:
        await ordering.set(new.order, set_by_user_id=admin.user_id if admin else None)
    except PriorityRefused as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    RequestAudit.of(request).set_target(target_type='extension_priority', target_id='deployment')
    RequestAudit.of(request).add_detail(new)
    return await ordering.view()
