"""Who may run commands in which VMs (`docs/service-identity.md` §13).

* a producer in its own VM — a person with their session, an app with
  its token;
* an org admin in any VM of their organisation;
* the super admin in any VM of the platform.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from pydantic import BaseModel

from app.api.base_v1.schema.vm_commands import Requester
from app.security.producer import ProducerCaller, ProducerRef

PRODUCER = 'producer'
ORG_ADMIN = 'org_admin'
SUPER_ADMIN = 'super_admin'


class Acting(BaseModel):
    """Who is asking, in which organisation, and as what."""

    org_id: str
    requester: Requester


class LogReading(BaseModel):
    """Whose commands in which organisation's audit log the caller reads."""

    org_id: str
    #: The one producer whose commands alone the caller may read; None for all of them.
    only: ProducerRef | None = None


class VmCommandAuthority:
    """The permission rules, in one place."""

    @staticmethod
    def _org(caller: ProducerCaller, org_id: str | None) -> str:
        org = org_id or caller.org_id
        if not org:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Name the organisation')
        return org

    @staticmethod
    def _acting(caller: ProducerCaller, org_id: str, acting_as: str) -> Acting:
        return Acting(org_id=org_id, requester=Requester(kind=caller.kind, id=caller.id, acting_as=acting_as))

    @staticmethod
    def _over_org(caller: ProducerCaller, org_id: str) -> str | None:
        """As what *caller* acts on every VM of *org_id*; None when they may not."""
        if org_id == caller.org_id and caller.is_org_admin:
            return ORG_ADMIN
        if caller.is_super_admin:
            return SUPER_ADMIN
        return None

    def over_one(self, caller: ProducerCaller, org_id: str | None, target: ProducerRef) -> Acting:
        org = self._org(caller, org_id)
        if org == caller.org_id and target == caller.ref:
            return self._acting(caller, org, PRODUCER)
        acting_as = self._over_org(caller, org)
        if acting_as is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='A producer runs commands only in its own VM; an org admin in their organisation',
            )
        return self._acting(caller, org, acting_as)

    def over_many(self, caller: ProducerCaller, org_id: str | None) -> Acting:
        org = self._org(caller, org_id)
        acting_as = self._over_org(caller, org)
        if acting_as is None:
            raise self._not_over_many()
        return self._acting(caller, org, acting_as)

    @staticmethod
    def _not_over_many() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail='Only an org admin, or the super admin, acts on many VMs'
        )

    def over_platform(self, caller: ProducerCaller) -> Acting:
        if not caller.is_super_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Only the super admin acts platform-wide')
        return self._acting(caller, '', SUPER_ADMIN)

    def over_log(self, caller: ProducerCaller, org_id: str | None) -> LogReading:
        """Org admins and the super admin read an organisation's audit log; a producer reads its own."""
        org = self._org(caller, org_id)
        if self._over_org(caller, org) is not None:
            return LogReading(org_id=org)
        if org != caller.org_id:
            raise self._not_over_many()
        return LogReading(org_id=org, only=caller.ref)
