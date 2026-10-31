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

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.api.base_v1.schema.portal.user import PortalAccount
from app.db.sql.models.registered_client import RegisteredClient
from app.db.sql.models.service_extension import ServiceExtension
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.service_extensions import ServiceExtensionDB
from app.middlewares.audit import set_audit_detail, set_audit_target
from app.security.deployment_admin import require_deployment_admin
from app.services.identity.extension_review import ExtensionReview, ExtensionReviews
from app.services.identity.extensions import ExtensionConflict, HeldUpdate, HeldUpdates
from app.services.identity.registry import ClientRegistry, ExtensionIdTaken

router = APIRouter()


class ExtensionSummary(BaseModel):
    extension_id: str
    version: str
    name: str
    description: str
    published_at: datetime

    @classmethod
    def of(cls, extension: ServiceExtension) -> ExtensionSummary:
        return cls(
            extension_id=extension.extension_id,
            version=extension.version,
            name=str(extension.manifest.get('name') or extension.extension_id),
            description=str(extension.manifest.get('description') or ''),
            published_at=extension.published_at,
        )


class ExtensionDetail(ExtensionSummary):
    """Everything the extension would put on people's machines.

    `manifest` is the whole manifest as the service sent it: what it
    adds (pages, menus, commands, indicators, settings, its prompt
    contributions), what it may touch (`permissions`) and what it needs
    (`requires`). `plugin` lists its own agents, the agents of igni's it
    stands in for, and its skills. `review` is its impact on igni and
    every collision it is in, with who wins it by priority.
    """

    sha256: str
    manifest: dict[str, object]
    plugin: dict[str, object]
    review: ExtensionReview | None = None


class ClientSummary(BaseModel):
    client_id: str
    kind: str
    name: str
    base_url: str
    redirect_uris: list[str]
    #: Compare with the one the client printed before approving.
    thumbprint: str
    state: str
    created_at: datetime
    decided_at: datetime | None = None
    #: What it ships to people's machines, if it has uploaded anything.
    extension: ExtensionSummary | None = None
    #: A newer version that asks for more, waiting for a super admin.
    held_version: str | None = None

    @classmethod
    def of(
        cls,
        client: RegisteredClient,
        extension: ServiceExtension | None = None,
        held: ServiceExtension | None = None,
    ) -> ClientSummary:
        return cls(
            client_id=client.client_id,
            kind=client.kind,
            name=client.name,
            base_url=client.base_url,
            redirect_uris=list(client.redirect_uris or []),
            thumbprint=client.thumbprint,
            state=client.state,
            created_at=client.created_at,
            decided_at=client.decided_at,
            extension=ExtensionSummary.of(extension) if extension is not None else None,
            held_version=held.version if held is not None else None,
        )


class ClientDetail(ClientSummary):
    extension_detail: ExtensionDetail | None = None
    #: The held version and what it adds, if one is waiting.
    held_update: HeldUpdate | None = None


@router.get('/clients', response_model=list[ClientSummary])
async def list_clients(_admin: PortalAccount | None = Depends(require_deployment_admin)) -> list[ClientSummary]:
    store = ServiceExtensionDB()
    extensions = await store.all_described()
    held = await store.all_held()
    return [
        ClientSummary.of(client, extensions.get(client.client_id), held.get(client.client_id))
        for client in await ClientRegistry().all()
    ]


@router.get('/held-updates', response_model=list[HeldUpdate])
async def held_updates(_admin: PortalAccount | None = Depends(require_deployment_admin)) -> list[HeldUpdate]:
    """Every held version, oldest first, each with what it asks for that the approved one did not."""
    return await HeldUpdates().all()


@router.get('/clients/{client_id}', response_model=ClientDetail)
async def client_detail(
    client_id: str, _admin: PortalAccount | None = Depends(require_deployment_admin)
) -> ClientDetail:
    client = await RegisteredClientDB().get(client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such client')
    store = ServiceExtensionDB()
    extension = await store.of(client_id)
    held = await HeldUpdates().of(client_id)
    summary = ClientSummary.of(client, extension, await store.held_of(client_id))
    detail = (
        ExtensionDetail(
            **ExtensionSummary.of(extension).model_dump(),
            sha256=extension.sha256,
            manifest=dict(extension.manifest),
            plugin=dict(extension.plugin),
            review=await ExtensionReviews().review(client_id),
        )
        if extension is not None
        else None
    )
    return ClientDetail(**summary.model_dump(), extension_detail=detail, held_update=held)


async def _decide(request: Request, client_id: str, decision: str, admin: PortalAccount | None) -> ClientSummary:
    registry = ClientRegistry()
    by = admin.user_id if admin else None
    try:
        client = (
            await registry.approve(client_id, by_user_id=by)
            if decision == 'approved'
            else await registry.revoke(client_id, by_user_id=by)
        )
    except ExtensionIdTaken as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such client')
    set_audit_target(request, target_type='registered_client', target_id=client_id)
    set_audit_detail(request, {'client': client.name, 'decision': decision, 'thumbprint': client.thumbprint})
    store = ServiceExtensionDB()
    return ClientSummary.of(client, await store.of(client_id), await store.held_of(client_id))


@router.post('/clients/{client_id}/approve', response_model=ClientSummary)
async def approve(
    request: Request, client_id: str, admin: PortalAccount | None = Depends(require_deployment_admin)
) -> ClientSummary:
    return await _decide(request, client_id, 'approved', admin)


@router.post('/clients/{client_id}/revoke', response_model=ClientSummary)
async def revoke(
    request: Request, client_id: str, admin: PortalAccount | None = Depends(require_deployment_admin)
) -> ClientSummary:
    return await _decide(request, client_id, 'revoked', admin)


@router.delete('/clients/{client_id}', status_code=status.HTTP_204_NO_CONTENT)
async def remove(
    request: Request, client_id: str, _admin: PortalAccount | None = Depends(require_deployment_admin)
) -> Response:
    """Forget a registration — a stray one, or one revoked for good.

    The client can register again with the same key, and starts over
    as pending.
    """
    if not await ClientRegistry().remove(client_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such client')
    set_audit_target(request, target_type='registered_client', target_id=client_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _decide_held(request: Request, client_id: str, decision: str) -> ClientSummary:
    """Approve or reject the version a service is waiting on, and say so in the audit trail."""
    client = await RegisteredClientDB().get(client_id)
    updates = HeldUpdates()
    held = await updates.of(client_id)
    if client is None or held is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='This service has no update waiting')
    if decision == 'approved':
        try:
            await updates.approve(client_id)
        except ExtensionConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    else:
        await updates.reject(client_id)
    set_audit_target(request, target_type='registered_client', target_id=client_id)
    set_audit_detail(
        request,
        {
            'client': client.name,
            'decision': f'held update {decision}',
            'extension': held.extension_id,
            'version': held.version,
            'sha256': held.sha256,
            'approved_version': held.approved_version,
            'added': [grant.text for grant in held.comparison.added],
        },
    )
    store = ServiceExtensionDB()
    return ClientSummary.of(client, await store.of(client_id), await store.held_of(client_id))


@router.post('/clients/{client_id}/held-update/approve', response_model=ClientSummary)
async def approve_held_update(
    request: Request, client_id: str, _admin: PortalAccount | None = Depends(require_deployment_admin)
) -> ClientSummary:
    """The held version becomes the approved one, and machines receive it."""
    return await _decide_held(request, client_id, 'approved')


@router.post('/clients/{client_id}/held-update/reject', response_model=ClientSummary)
async def reject_held_update(
    request: Request, client_id: str, _admin: PortalAccount | None = Depends(require_deployment_admin)
) -> ClientSummary:
    """The held version is discarded; the service stays on the approved one."""
    return await _decide_held(request, client_id, 'rejected')
