"""Pydantic schemas for the organisations a service serves (`docs/service-identity.md` §21)."""

from pydantic import BaseModel


class ServedOrganisation(BaseModel):
    org_id: str
    name: str


class ServedOrganisations(BaseModel):
    organisations: list[ServedOrganisation]
