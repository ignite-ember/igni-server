"""An organisation's apps — the producers that are not people.

Org admin only, in the organisation the portal is acting in. An app is
registered with a name, an owner and its public keys, gets tokens as
itself (`client_credentials`, `private_key_jwt`), is in no group, and has
its own list of extensions, off until switched on here
(`docs/service-identity.md` §11).
"""

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.base_v1.schema.audit_detail import (
    AppDiscardedAudit,
    AppRegisteredAudit,
    GroupExtensionSetAudit,
    KeysReplacedAudit,
)
from app.api.base_v1.schema.portal.apps import (
    DiscardAppRequest,
    DiscardedApp,
    OrgAppList,
    OrgAppResponse,
    RegisterAppRequest,
    ReplaceAppKeysRequest,
    UpdateAppRequest,
)
from app.api.base_v1.schema.portal.groups import GroupExtensionList, SetGroupExtension
from app.api.base_v1.schema.portal.user import PortalUser
from app.middlewares.request_audit import RequestAudit
from app.security.portal_auth import require_admin
from app.services.portal.apps import OrgApps

router = APIRouter()


@router.get('/apps', response_model=OrgAppList)
async def list_apps(current_user: PortalUser = Depends(require_admin)) -> OrgAppList:
    return await OrgApps().list(current_user.org_id)


@router.post('/apps', response_model=OrgAppResponse, status_code=status.HTTP_201_CREATED)
async def register_app(
    request: Request, body: RegisterAppRequest, current_user: PortalUser = Depends(require_admin)
) -> OrgAppResponse:
    app = await OrgApps().register(current_user.org_id, body, by_user_id=current_user.user_id)
    RequestAudit.of(request).set_target(target_type='app', target_id=app.app_id)
    RequestAudit.of(request).add_detail(AppRegisteredAudit(app=app.name, thumbprint=app.thumbprint))
    return app


@router.get('/apps/{app_id}', response_model=OrgAppResponse)
async def get_app(app_id: str, current_user: PortalUser = Depends(require_admin)) -> OrgAppResponse:
    return await OrgApps().get(current_user.org_id, app_id)


@router.patch('/apps/{app_id}', response_model=OrgAppResponse)
async def update_app(
    request: Request, app_id: str, body: UpdateAppRequest, current_user: PortalUser = Depends(require_admin)
) -> OrgAppResponse:
    RequestAudit.of(request).set_target(target_type='app', target_id=app_id)
    return await OrgApps().update(current_user.org_id, app_id, body)


@router.put('/apps/{app_id}/keys', response_model=OrgAppResponse)
async def replace_app_keys(
    request: Request, app_id: str, body: ReplaceAppKeysRequest, current_user: PortalUser = Depends(require_admin)
) -> OrgAppResponse:
    app = await OrgApps().replace_keys(current_user.org_id, app_id, body)
    RequestAudit.of(request).set_target(target_type='app', target_id=app_id)
    RequestAudit.of(request).add_detail(KeysReplacedAudit(thumbprint=app.thumbprint))
    return app


@router.post('/apps/{app_id}/discard', response_model=DiscardedApp)
async def discard_app(
    request: Request,
    app_id: str,
    body: DiscardAppRequest | None = None,
    current_user: PortalUser = Depends(require_admin),
) -> DiscardedApp:
    """Removed at once — never woken again, its place under the licence free — and its VM erased in the background."""
    start_fresh = bool(body and body.start_fresh)
    RequestAudit.of(request).set_target(target_type='app', target_id=app_id)
    discarded = await OrgApps().discard(
        current_user.org_id, app_id, by_user_id=current_user.user_id, start_fresh=start_fresh
    )
    RequestAudit.of(request).add_detail(
        AppDiscardedAudit(
            erasure=discarded.discarded.state if discarded.discarded else None,
            fresh_app=discarded.fresh_app.app_id if discarded.fresh_app else None,
        )
    )
    return discarded


@router.delete('/apps/{app_id}', status_code=status.HTTP_204_NO_CONTENT)
async def remove_app(request: Request, app_id: str, current_user: PortalUser = Depends(require_admin)) -> Response:
    """The same as discarding it, without a fresh one."""
    RequestAudit.of(request).set_target(target_type='app', target_id=app_id)
    await OrgApps().discard(current_user.org_id, app_id, by_user_id=current_user.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/apps/{app_id}/extensions', response_model=GroupExtensionList)
async def list_app_extensions(app_id: str, current_user: PortalUser = Depends(require_admin)) -> GroupExtensionList:
    """Every approved extension, with whether this app has it on."""
    return await OrgApps().extensions(current_user.org_id, app_id)


@router.put('/apps/{app_id}/extensions/{extension_id}', status_code=status.HTTP_204_NO_CONTENT)
async def set_app_extension(
    request: Request,
    app_id: str,
    extension_id: str,
    body: SetGroupExtension,
    current_user: PortalUser = Depends(require_admin),
) -> Response:
    await OrgApps().set_extension(
        current_user.org_id, app_id, extension_id, body.enabled, by_user_id=current_user.user_id
    )
    RequestAudit.of(request).set_target(target_type='app', target_id=app_id)
    RequestAudit.of(request).add_detail(GroupExtensionSetAudit(extension=extension_id, enabled=body.enabled))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
