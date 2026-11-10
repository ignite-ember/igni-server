"""Answering a subject access request. GDPR Article 15.

DP-8 in `COMPLIANCE_TRACKER.md`. The customer running igni is the
controller and receives the request; this is what they need in order to
answer it. Before this the only option was SQL across twenty tables.

## POST, not GET, and the address in the body

A subject access request is a privileged read of somebody's personal
data, and three things follow from putting the address in a body rather
than a query string:

* it does not land in the reverse proxy's access log, or in browser
  history, or in a referrer header — a `GET ?email=` would put the
  subject's address in all three, which is a disclosure created by the
  act of answering a disclosure request;
* F90's audit rule records mutations on `/v1/portal`, so a `POST` is
  audited automatically. Somebody exporting a person's data is exactly
  what an auditor wants recorded;
* the same reasoning the erasure endpoint follows.

## Admin-only

The report contains addresses, roles, sign-in times and authored commit
messages. `require_admin`, and the caller's organisation is recorded on
the report for provenance.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, EmailStr, Field

from app.api.base_v1.schema.portal.user import PortalUser
from app.middlewares.request_audit import RequestAudit
from app.security.portal_auth import require_admin
from app.services.subject_access.schemas import SubjectAccessReport
from app.services.subject_access.service import SubjectAccessService
from app.utils.email_mask import mask_email

logger = logging.getLogger(__name__)

router = APIRouter()


def subject_access_service() -> SubjectAccessService:
    """One service per request.

    A provider rather than `Depends(SubjectAccessService)`: FastAPI would
    read the constructor's keyword arguments as query parameters.
    """
    return SubjectAccessService()


class SubjectAccessAudit(BaseModel):
    """What the audit trail records about an export.

    Masked and counted, never the address: F90 retains these rows for
    the whole window, so an export of somebody's data must not leave
    their email sitting in the record of it. This was a nested dict
    literal inside the audit detail call — a retained record
    with a fixed key set, described by its own construction.
    """

    subject: str
    sections: int
    has_account: bool


class SubjectAccessAuditDetail(BaseModel):
    """The audit detail an export leaves, under its own key."""

    subject_access: SubjectAccessAudit


class SubjectAccessRequest(BaseModel):
    email: EmailStr = Field(description='The address the request concerns.')


@router.post(
    '/subject-access',
    response_model=SubjectAccessReport,
    description=(
        'Everything this deployment holds about one person, with the Article 15 '
        'context: purposes, categories, recipients, retention and source. '
        'Credentials are withheld and named as withheld; commits by other people '
        'in the same push are excluded.'
    ),
)
async def subject_access(
    request: Request,
    body: SubjectAccessRequest = Body(...),
    current_user: PortalUser = Depends(require_admin),
    service: SubjectAccessService = Depends(subject_access_service),
) -> SubjectAccessReport:
    email = str(body.email)

    report = await service.export(email=email, org_id=current_user.org_id)

    # Added to the audit record F90 writes for this request. Masked and
    # counted: the audit trail is retained, so an export of somebody's
    # data must not leave their address sitting in it.
    RequestAudit.of(request).add_detail(
        SubjectAccessAuditDetail(
            subject_access=SubjectAccessAudit(
                subject=mask_email(email),
                sections=report.sections.present_count(),
                has_account=report.sections.has_account(),
            )
        )
    )
    logger.info(
        'Subject access export by %s for %s (%d sections)',
        current_user.user_id,
        mask_email(email),
        report.sections.present_count(),
    )

    return report
