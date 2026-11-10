"""Exports of a removed member's data on the portal (`docs/service-identity.md` §18.4)."""

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from app.api.base_v1.schema.audit_detail import ExportRequestedAudit
from app.api.base_v1.schema.retention import ExportList, ExportRecord, ExportRequest
from app.middlewares.request_audit import RequestAudit
from app.security.org_authority import OrgAuthority, require_org_authority
from app.services.cloud.producer_exports import ProducerExports

router = APIRouter()


@router.post(
    '/orgs/{org_id}/producers/person/{user_id}/exports',
    response_model=ExportRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_export(
    request: Request, user_id: str, body: ExportRequest, authority: OrgAuthority = Depends(require_org_authority)
) -> ExportRecord:
    """Ask for an export of a removed incarnation's data while it waits for erasure."""
    export = await ProducerExports().request(authority, user_id, body.incarnation)
    RequestAudit.of(request).set_target(target_type='producer_export', target_id=export.export_id)
    RequestAudit.of(request).add_detail(ExportRequestedAudit(person=user_id, incarnation=export.incarnation))
    return export


@router.get('/orgs/{org_id}/exports', response_model=ExportList)
async def list_exports(authority: OrgAuthority = Depends(require_org_authority)) -> ExportList:
    return await ProducerExports().list(authority.org_id)


@router.get('/orgs/{org_id}/exports/{export_id}', response_model=ExportRecord)
async def get_export(export_id: str, authority: OrgAuthority = Depends(require_org_authority)) -> ExportRecord:
    return await ProducerExports().get(authority.org_id, export_id)


@router.get('/orgs/{org_id}/exports/{export_id}/download')
async def download(
    request: Request, export_id: str, authority: OrgAuthority = Depends(require_org_authority)
) -> StreamingResponse:
    row, chunks = await ProducerExports().archive(authority.org_id, export_id)
    RequestAudit.of(request).set_target(target_type='producer_export', target_id=export_id)
    return StreamingResponse(
        chunks,
        media_type='application/octet-stream',
        headers={
            'Content-Length': str(row.size or 0),
            'Content-Disposition': f'attachment; filename="export-{export_id}.tar.gz"',
        },
    )
