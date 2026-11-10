"""Schemas for the portal's organisation endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateOrgRequest(BaseModel):
    display_name: str
    slug: str | None = None
    #: The email domains it owns. A person belongs to the organisation
    #: owning their address's domain, so an organisation with none could
    #: only ever have its creator in it.
    domains: list[str] = Field(min_length=1, max_length=20)


class UpdateOrgRequest(BaseModel):
    display_name: str | None = None
    slug: str | None = None


class DomainRequest(BaseModel):
    domain: str


class CreateOrgResponse(BaseModel):
    """An organisation as its admin sees it after creating or changing it."""

    id: str
    slug: str
    display_name: str


class OrgDomains(BaseModel):
    org_id: str
    domains: list[str]


class DeploymentOrg(BaseModel):
    """One organisation as a super admin sees it: what it is and what it owns."""

    id: str
    slug: str
    display_name: str
    domains: list[str]
    members: int


class DeleteOrgResponse(BaseModel):
    message: str
