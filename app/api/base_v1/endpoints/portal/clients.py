"""Registered clients: what is waiting, and a super admin's decision.

Deployment-wide, so `require_deployment_admin`: a super admin, or the
break-glass key when nobody can sign in. Approving lets the client
obtain tokens for any organisation on the install, which is why it is
not an organisation admin's to give.

An approved service's new version that asks for more than the approved
one is held (`app.services.identity.extensions`); the super admin
approves or rejects it here too — `GET /portal/held-updates` lists them
with what each adds, and `POST /portal/clients/{id}/held-update/approve`
or `/reject` decides one.

Each mutation is recorded by the audit middleware; the handler names
the client and the decision so the row says more than a method and a
path.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.base_v1.schema.portal.clients import ClientDetail, ClientSummary
from app.api.base_v1.schema.portal.user import PortalAccount
from app.middlewares.audit import set_audit_detail, set_audit_target
from app.security.deployment_admin import require_deployment_admin
from app.services.identity.client_review import (
    client_review,
    ClientNotFound,
    ClientReview,
    HeldDecision,
    NoHeldUpdate,
    ReviewedClient,
)
from app.services.identity.extensions import ExtensionConflict, HeldUpdate
from app.services.identity.registry import ExtensionIdTaken

router = APIRouter()

_NO_SUCH_CLIENT = 'No such client'
_NO_HELD_UPDATE = 'This service has no update waiting'


@router.get('/clients', response_model=list[ClientSummary])
async def list_clients(
    _admin: PortalAccount | None = Depends(require_deployment_admin),
    review: ClientReview = Depends(client_review),
) -> list[ClientSummary]:
    return [ClientSummary.of(reviewed) for reviewed in await review.all()]


@router.get('/held-updates', response_model=list[HeldUpdate])
async def held_updates(
    _admin: PortalAccount | None = Depends(require_deployment_admin),
    review: ClientReview = Depends(client_review),
) -> list[HeldUpdate]:
    """Every held version, oldest first, each with what it asks for that the approved one did not."""
    return await review.held_updates()


@router.get('/clients/{client_id}', response_model=ClientDetail)
async def client_detail(
    client_id: str,
    _admin: PortalAccount | None = Depends(require_deployment_admin),
    review: ClientReview = Depends(client_review),
) -> ClientDetail:
    try:
        dossier = await review.dossier(client_id)
    except ClientNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_SUCH_CLIENT) from exc
    return ClientDetail.of_dossier(dossier)


@router.post('/clients/{client_id}/approve', response_model=ClientSummary)
async def approve(
    request: Request,
    client_id: str,
    admin: PortalAccount | None = Depends(require_deployment_admin),
    review: ClientReview = Depends(client_review),
) -> ClientSummary:
    try:
        reviewed = await review.approve(client_id, by_user_id=admin.user_id if admin else None)
    except ExtensionIdTaken as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ClientNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_SUCH_CLIENT) from exc
    _audit_decision(request, reviewed=reviewed, decision='approved')
    return ClientSummary.of(reviewed)


@router.post('/clients/{client_id}/revoke', response_model=ClientSummary)
async def revoke(
    request: Request,
    client_id: str,
    admin: PortalAccount | None = Depends(require_deployment_admin),
    review: ClientReview = Depends(client_review),
) -> ClientSummary:
    try:
        reviewed = await review.revoke(client_id, by_user_id=admin.user_id if admin else None)
    except ClientNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_SUCH_CLIENT) from exc
    _audit_decision(request, reviewed=reviewed, decision='revoked')
    return ClientSummary.of(reviewed)


@router.delete('/clients/{client_id}', status_code=status.HTTP_204_NO_CONTENT)
async def remove(
    request: Request,
    client_id: str,
    _admin: PortalAccount | None = Depends(require_deployment_admin),
    review: ClientReview = Depends(client_review),
) -> Response:
    """Forget a registration — a stray one, or one revoked for good.

    The client can register again with the same key, and starts over
    as pending.
    """
    try:
        await review.remove(client_id)
    except ClientNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_SUCH_CLIENT) from exc
    set_audit_target(request, target_type='registered_client', target_id=client_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post('/clients/{client_id}/held-update/approve', response_model=ClientSummary)
async def approve_held_update(
    request: Request,
    client_id: str,
    _admin: PortalAccount | None = Depends(require_deployment_admin),
    review: ClientReview = Depends(client_review),
) -> ClientSummary:
    """The held version becomes the approved one, and machines receive it."""
    try:
        decision = await review.approve_held(client_id)
    except NoHeldUpdate as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_HELD_UPDATE) from exc
    except ExtensionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _audit_held_decision(request, decision=decision, outcome='approved')
    return ClientSummary.of(decision.reviewed)


@router.post('/clients/{client_id}/held-update/reject', response_model=ClientSummary)
async def reject_held_update(
    request: Request,
    client_id: str,
    _admin: PortalAccount | None = Depends(require_deployment_admin),
    review: ClientReview = Depends(client_review),
) -> ClientSummary:
    """The held version is discarded; the service stays on the approved one."""
    try:
        decision = await review.reject_held(client_id)
    except NoHeldUpdate as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_HELD_UPDATE) from exc
    _audit_held_decision(request, decision=decision, outcome='rejected')
    return ClientSummary.of(decision.reviewed)


def _audit_decision(request: Request, *, reviewed: ReviewedClient, decision: str) -> None:
    client = reviewed.client
    set_audit_target(request, target_type='registered_client', target_id=client.client_id)
    set_audit_detail(request, {'client': client.name, 'decision': decision, 'thumbprint': client.thumbprint})


def _audit_held_decision(request: Request, *, decision: HeldDecision, outcome: str) -> None:
    client, held = decision.reviewed.client, decision.decided
    set_audit_target(request, target_type='registered_client', target_id=client.client_id)
    set_audit_detail(
        request,
        {
            'client': client.name,
            'decision': f'held update {outcome}',
            'extension': held.extension_id,
            'version': held.version,
            'sha256': held.sha256,
            'approved_version': held.approved_version,
            'added': [grant.text for grant in held.comparison.added],
        },
    )
