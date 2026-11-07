"""Erasing one person's data on request.

DP-7 in `COMPLIANCE_TRACKER.md` — GDPR Article 17. The customer running
igni is the controller and receives the request; this is the mechanism
they need in order to act on it. Deleting a `users` row already works and
provenance degrades to NULL rather than cascading (F60, F85). What this
reaches is the rest: the services that hold a person's work. The workflow
itself is `app.services.identity.personal_erasure`.

## Redaction, not deletion

A document carries other people's comments, and a departing author must
not take them with him. Article 17 asks that the personal data be gone;
"this runbook exists" is not personal data. So the services redact.

## Admin-only and audited

`require_admin`. The action is a `POST` under `/v1/portal`, so F90's
middleware records it without this module doing anything: who ran an
erasure, for whom, and when is exactly the kind of thing an auditor asks
about afterwards. This module adds the masked subject and the outcome.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from app.api.base_v1.schema.portal.erasure import (
    ErasureDoneAudit,
    ErasureRefusedAudit,
    ErasureRequest,
    ErasureResult,
)
from app.api.base_v1.schema.portal.user import PortalUser
from app.middlewares.audit import set_audit_detail
from app.security.portal_auth import require_admin
from app.services.cloud.legal_holds import ErasureRefused
from app.services.identity.personal_erasure import PersonalErasure
from app.services.identity.service_subjects import ErasureIncomplete
from app.utils.email_mask import mask_email

logger = logging.getLogger(__name__)

router = APIRouter()


def personal_erasure() -> PersonalErasure:
    """One service per request.

    A provider rather than `Depends(PersonalErasure)`: FastAPI would read
    the constructor's keyword arguments as query parameters.
    """
    return PersonalErasure()


@router.post(
    '/erasure',
    response_model=ErasureResult,
    description=(
        'Erase one person from the services that hold their work (GDPR Art. 17). '
        "Redacted in place, because other people's work is in the same records."
    ),
)
async def erase_person(
    request: Request,
    body: ErasureRequest = Body(...),
    current_user: PortalUser = Depends(require_admin),
    service: PersonalErasure = Depends(personal_erasure),
) -> ErasureResult:
    email = str(body.email)
    masked = mask_email(email)

    try:
        services_told = await service.erase(email, org_id=current_user.org_id)
    except ErasureRefused as refused:
        # The refusal is in the audit trail with the reason.
        set_audit_detail(request, {'erasure': ErasureRefusedAudit(subject=masked, refused=str(refused)).model_dump()})
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refused)) from refused
    except ErasureIncomplete as exc:
        # Named, so the operator knows which service to look at.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'The erasure was not confirmed by: {", ".join(exc.services)}. Run it again.',
        ) from exc

    # Added to the audit record F90 writes for this request. Masked —
    # the audit trail is retained, so it must not become the place the
    # erased address survives.
    set_audit_detail(request, {'erasure': ErasureDoneAudit(subject=masked, services=services_told).model_dump()})
    logger.info(
        'Erasure by %s for %s; services told: %s',
        current_user.user_id,
        masked,
        ', '.join(services_told) or 'none',
    )
    return ErasureResult(email=masked, services_told=services_told)
