"""Pydantic schemas for a person's MCP server approvals (`docs/service-identity.md` §14)."""

from datetime import datetime

from pydantic import BaseModel, Field


class McpServerRef(BaseModel):
    """One MCP server, as igni names it: its name and the configuration it came from."""

    server: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=1000)


class McpApproval(McpServerRef):
    approved_at: datetime


class McpApprovalList(BaseModel):
    approvals: list[McpApproval]
