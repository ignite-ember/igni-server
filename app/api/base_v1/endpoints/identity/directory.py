"""The directory: one organisation's people, for the services serving it.

Every route depends on `require_service`, and every answer is the token's
organisation's — the organisation is never a parameter. A service that
needs another organisation's people asks for a token for that one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.db.sql.directory import OrgDirectoryDB
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.user import User
from app.db.sql.org_domains import OrgDomainDB
from app.db.sql.organizations import OrganizationDB
from app.db.sql.tenant_registry import TenantRegistryDB
from app.security.service_caller import require_service, ServiceCaller

router = APIRouter(prefix='/directory')

#: The most people one call returns.
MAX_PEOPLE = 500


class Organisation(BaseModel):
    org_id: str
    name: str
    slug: str
    #: Assigned once and never reused. A service derives its own
    #: per-organisation database and Redis index from it.
    tenant_number: int
    domains: list[str]


class Person(BaseModel):
    user_id: str
    email: str
    name: str
    role: str
    #: `active`, or `deactivated` for somebody who has left.
    status: str
    super_admin: bool

    @classmethod
    def of(cls, user: User, membership: OrgMembership) -> Person:
        return cls(
            user_id=str(user.user_id),
            email=str(user.email or ''),
            name=str(user.full_name or ''),
            role=str(membership.role),
            status=str(membership.status),
            super_admin=bool(user.is_super_admin),
        )


class People(BaseModel):
    people: list[Person] = Field(default_factory=list)


class ResolveRequest(BaseModel):
    identities: list[str] = Field(default_factory=list, max_length=MAX_PEOPLE)


@router.get('/organisation', response_model=Organisation)
async def organisation(caller: ServiceCaller = Depends(require_service)) -> Organisation:
    found = await OrganizationDB().get_by_id(caller.org_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='This organisation no longer exists')
    return Organisation(
        org_id=str(found.id),
        name=str(found.display_name),
        slug=str(found.slug),
        tenant_number=await TenantRegistryDB().number_of(caller.org_id),
        domains=await OrgDomainDB().domains_of(caller.org_id),
    )


@router.get('/members', response_model=People)
async def members(
    query: str = Query(default='', max_length=200),
    limit: int = Query(default=100, ge=1, le=MAX_PEOPLE),
    include_former: bool = Query(default=False),
    caller: ServiceCaller = Depends(require_service),
) -> People:
    found = await OrgDirectoryDB().members(caller.org_id, query=query, limit=limit, include_former=include_former)
    return People(people=[Person.of(user, membership) for user, membership in found])


@router.get('/people/{user_id}', response_model=Person)
async def person(user_id: str, caller: ServiceCaller = Depends(require_service)) -> Person:
    found = await OrgDirectoryDB().person(caller.org_id, user_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Not a member of this organisation')
    return Person.of(*found)


@router.post('/people/resolve', response_model=People)
async def resolve(body: ResolveRequest, caller: ServiceCaller = Depends(require_service)) -> People:
    identities = [value.strip() for value in body.identities if value.strip()]
    if not identities:
        return People()
    found = await OrgDirectoryDB().resolve(caller.org_id, identities)
    return People(people=[Person.of(user, membership) for user, membership in found])
