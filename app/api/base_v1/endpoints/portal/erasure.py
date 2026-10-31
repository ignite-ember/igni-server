"""Erasing one person's data on request.

DP-7 in `COMPLIANCE_TRACKER.md` — GDPR Article 17. The customer running
igni is the controller and receives the request; this is the mechanism
they need in order to act on it. Deleting a `users` row already works and
provenance degrades to NULL rather than cascading (F60, F85). What this
reaches is the rest: the services that hold a person's work.

## Redaction, not deletion

A document carries other people's comments, and a departing author must
not take them with him. Article 17 asks that the personal data be gone;
"this runbook exists" is not personal data. So the services redact.

## Admin-only and audited

`require_admin`. The action is a `POST` under `/v1/portal`, so F90's
middleware records it without this module doing anything: who ran an
erasure, for whom, and when is exactly the kind of thing an auditor asks
about afterwards.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.api.base_v1.schema.portal.user import PortalUser
from app.db.sql.user import UserDB
from app.middlewares.audit import set_audit_detail
from app.security.portal_auth import require_admin
from app.services.cloud.legal_holds import LegalHolds
from app.services.cloud.producer_feed import ProducerFeed
from app.services.identity.service_subjects import ErasureIncomplete, ServiceSubjects

logger = logging.getLogger(__name__)

router = APIRouter()

_users = UserDB()


class ErasureRequest(BaseModel):
    email: EmailStr = Field(description='The address of the person to erase.')


class ErasureResult(BaseModel):
    email: str = Field(description='Echoed masked, so the response is not itself a disclosure.')
    services_told: list[str] = Field(
        default_factory=list,
        description='The registered services that confirmed the erasure, by name.',
    )


def _mask(email: str) -> str:
    local, _, domain = email.partition('@')
    return f'{local[:1]}***@{domain}' if domain else '***'


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
) -> ErasureResult:
    email = str(body.email)

    # Raises rather than swallowing, unlike most best-effort calls in
    # this codebase. Erasure is a legal obligation, and an operator
    # told it succeeded must not have been told that because a request
    # timed out. A deployment with no approved services is a no-op,
    # which is correct: there is nothing anywhere else to redact.
    subject = await _users.get_by_email(email)
    subject_id = str(subject.user_id) if subject else None
    services_told: list[str] = []
    if subject_id:
        # Overrides the organisation's retention — but not a legal hold:
        # refused, and the refusal is in the audit trail with the reason.
        try:
            await LegalHolds().refuse_erasure_if_held(subject_id)
        except HTTPException as refused:
            set_audit_detail(request, {'erasure': {'subject': _mask(email), 'refused': str(refused.detail)}})
            raise
        try:
            services_told = await ServiceSubjects().erase(subject_id, org_id=current_user.org_id)
        except ErasureIncomplete as exc:
            # Named, so the operator knows which service to look at;
            # and safe to run again, because every service must make
            # erasure idempotent.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f'The erasure was not confirmed by: {", ".join(exc.services)}. Run it again.',
            ) from exc
        # The gateway's feed names them: removed now, so the gateway erases
        # their VMs, and the rows go once it has been told.
        await ProducerFeed().erase_person(subject_id)

    # Added to the audit record F90 writes for this request. Masked —
    # the audit trail is retained, so it must not become the place the
    # erased address survives.
    set_audit_detail(
        request,
        {'erasure': {'subject': _mask(email), 'services': services_told}},
    )
    logger.info(
        'Erasure by %s for %s; services told: %s',
        current_user.user_id,
        _mask(email),
        ', '.join(services_told) or 'none',
    )
    return ErasureResult(email=_mask(email), services_told=services_told)
