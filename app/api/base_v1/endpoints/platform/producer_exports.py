"""The node agent's side of exports (`docs/service-identity.md` §18.4). A platform token only; audited."""

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.base_v1.schema.retention import ExportFailed, ExportRecord, PendingExports
from app.middlewares.audit import set_audit_target
from app.security.platform_caller import PlatformCaller, require_platform
from app.services.cloud.producer_exports import ProducerExports

router = APIRouter()


@router.post('/exports/pull', response_model=PendingExports)
async def pull(platform: PlatformCaller = Depends(require_platform)) -> PendingExports:
    """Exports to run, each handed to the caller for 30 minutes."""
    return await ProducerExports().pending(by_client_id=platform.client_id)


@router.put('/orgs/{org_id}/exports/{export_id}/archive', response_model=ExportRecord)
async def upload(
    request: Request, org_id: str, export_id: str, _platform: PlatformCaller = Depends(require_platform)
) -> ExportRecord:
    """The archive `export-producer` wrote, as the body."""
    set_audit_target(request, target_type='producer_export', target_id=export_id)
    return await ProducerExports().upload(org_id, export_id, request.stream())


@router.post('/orgs/{org_id}/exports/{export_id}/failed', status_code=status.HTTP_204_NO_CONTENT)
async def failed(
    request: Request,
    org_id: str,
    export_id: str,
    body: ExportFailed,
    _platform: PlatformCaller = Depends(require_platform),
) -> Response:
    set_audit_target(request, target_type='producer_export', target_id=export_id)
    await ProducerExports().failed(org_id, export_id, body.error)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
