"""A person's MCP approvals, in their organisation's own database."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.sql.models.person_mcp_approval import PersonMcpApproval
from app.db.sql.tenancy import TenantData


class McpApprovalsDB(TenantData):
    async def of(self, org_id: str, user_id: str) -> list[PersonMcpApproval]:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(PersonMcpApproval)
                .where(PersonMcpApproval.user_id == user_id)
                .order_by(PersonMcpApproval.server_name, PersonMcpApproval.source)
            )
            return list(rows.scalars().all())

    async def approve(self, org_id: str, user_id: str, server: str, source: str) -> None:
        async with self.session_scope(org_id) as session:
            await session.execute(
                insert(PersonMcpApproval)
                .values(user_id=user_id, server_name=server, source=source)
                .on_conflict_do_nothing(constraint='uq_person_mcp_approvals_user_server_source')
            )

    async def withdraw(self, org_id: str, user_id: str, server: str, source: str) -> None:
        async with self.session_scope(org_id) as session:
            await session.execute(
                delete(PersonMcpApproval).where(
                    PersonMcpApproval.user_id == user_id,
                    PersonMcpApproval.server_name == server,
                    PersonMcpApproval.source == source,
                )
            )
