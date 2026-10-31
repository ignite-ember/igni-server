"""A person's approvals of MCP servers, kept on igni server for the cloud.

igni in a person's microVM reads them here instead of from a file under
`~/.igni`, and records a new one here when the person approves a server.
Only people approve: an app has no one to ask. Group policy still decides
which servers are offered at all; an approval only says the person said
yes to one that was (`docs/service-identity.md` §14).
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.api.base_v1.schema.mcp_approvals import McpApproval, McpApprovalList, McpServerRef
from app.db.sql.mcp_approvals import McpApprovalsDB
from app.security.producer import ProducerCaller
from app.services.auth.jwt_service import PERSON


class McpApprovals:
    def __init__(self) -> None:
        self._db = McpApprovalsDB()

    @staticmethod
    def _person(caller: ProducerCaller) -> tuple[str, str]:
        if caller.kind != PERSON or not caller.org_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Only a person approves MCP servers')
        return caller.org_id, caller.id

    async def of(self, caller: ProducerCaller) -> McpApprovalList:
        org_id, user_id = self._person(caller)
        return McpApprovalList(
            approvals=[
                McpApproval(server=row.server_name, source=row.source, approved_at=row.approved_at)
                for row in await self._db.of(org_id, user_id)
            ]
        )

    async def approve(self, caller: ProducerCaller, server: McpServerRef) -> McpApprovalList:
        org_id, user_id = self._person(caller)
        await self._db.approve(org_id, user_id, server.server, server.source)
        return await self.of(caller)

    async def withdraw(self, caller: ProducerCaller, server: McpServerRef) -> None:
        org_id, user_id = self._person(caller)
        await self._db.withdraw(org_id, user_id, server.server, server.source)
