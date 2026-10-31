"""`/v1/me/mcp-approvals`: the signed-in person's approvals of MCP servers.

Read and written by igni in the person's cloud VM with their session
(`docs/service-identity.md` §14).
"""

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.base_v1.schema.mcp_approvals import McpApprovalList, McpServerRef
from app.security.producer import ProducerCaller, require_producer
from app.services.cloud.mcp_approvals import McpApprovals

router = APIRouter(prefix='/me/mcp-approvals')


@router.get('', response_model=McpApprovalList)
async def list_approvals(caller: ProducerCaller = Depends(require_producer)) -> McpApprovalList:
    return await McpApprovals().of(caller)


@router.post('', response_model=McpApprovalList)
async def approve(body: McpServerRef, caller: ProducerCaller = Depends(require_producer)) -> McpApprovalList:
    """Approve one server from one configuration. Approving it again changes nothing."""
    return await McpApprovals().approve(caller, body)


@router.delete('', status_code=status.HTTP_204_NO_CONTENT)
async def withdraw(
    server: str = Query(min_length=1, max_length=200),
    source: str = Query(min_length=1, max_length=1000),
    caller: ProducerCaller = Depends(require_producer),
) -> Response:
    await McpApprovals().withdraw(caller, McpServerRef(server=server, source=source))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
