"""An app's spaces: one producer per end user of the app (`docs/service-identity.md` §17).

An app declares a space for each of its own users. A space is a full
producer — its own VM, volume, keys and database login — owned by the
app, in the app's organisation and in no group, inheriting the app's VM
profile and extensions. The app declares, reads, lists and deletes them
with its own token, and acts as one by exchanging that token for the
space's. Deleting a space revokes its VM credential, drops its pending
events and, through the feed, tells the gateway to erase its VM.

Limits: the super admin caps spaces per app and per organisation; an
org admin may set lower ones for their organisation.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status

from app.api.base_v1.schema.spaces import (
    SpaceCapsBody,
    SpaceCapsResponse,
    SpaceLimit,
    SpacePage,
    SpaceQuotaBody,
    SpaceQuotaResponse,
    SpaceRecord,
)
from app.db.sql.models.app_space import AppSpace, SPACE
from app.db.sql.models.producer_event import PRODUCER_REMOVED
from app.db.sql.models.registered_client import APP
from app.db.sql.models.space_limits import OrgSpaceQuota, PlatformSpaceCaps
from app.db.sql.models.vm_credential import SPACE_REMOVED
from app.db.sql.producer_events import ProducerEventsDB
from app.db.sql.revocations import FeedSignals
from app.db.sql.spaces import SpaceLimitReached, SpaceLimitsDB, SpacesDB
from app.db.sql.vm_credentials import VmCredentialsDB
from app.security.producer import ProducerCaller, ProducerRef
from app.services.cloud.vm_credentials import VmCredentialRecord, VmCredentials


def _smaller(cap: int | None, own: int | None) -> int | None:
    """The tighter of two limits, where None is no limit."""
    return own if cap is None else cap if own is None else min(cap, own)


class SpaceLimits:
    """The platform's caps on spaces and each organisation's own limits."""

    def __init__(self) -> None:
        self._db = SpaceLimitsDB()

    async def caps(self) -> SpaceCapsResponse:
        row = await self._db.caps()
        if row is None:
            return SpaceCapsResponse()
        return SpaceCapsResponse(
            max_spaces_per_app=row.max_spaces_per_app,
            max_spaces_per_org=row.max_spaces_per_org,
            updated_at=row.updated_at,
        )

    async def set_caps(self, body: SpaceCapsBody, *, by_user_id: str) -> SpaceCapsResponse:
        await self._db.put_caps(PlatformSpaceCaps(**body.model_dump(), updated_by_user_id=by_user_id))
        return await self.caps()

    async def quota(self, org_id: str) -> SpaceQuotaResponse:
        row = await self._db.quota(org_id)
        return SpaceQuotaResponse(
            org_id=org_id,
            max_spaces_per_app=row.max_spaces_per_app if row else None,
            max_spaces=row.max_spaces if row else None,
            caps=await self.caps(),
            spaces=await SpacesDB().count(org_id),
            updated_at=row.updated_at if row else None,
        )

    async def set_quota(self, org_id: str, body: SpaceQuotaBody, *, by_user_id: str) -> SpaceQuotaResponse:
        caps = await self.caps()
        for own, cap, what in (
            (body.max_spaces_per_app, caps.max_spaces_per_app, 'per app'),
            (body.max_spaces, caps.max_spaces_per_org, 'in an organisation'),
        ):
            if own is not None and cap is not None and own > cap:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f'The platform allows at most {cap} spaces {what}',
                )
        await self._db.put_quota(OrgSpaceQuota(org_id=org_id, **body.model_dump(), updated_by_user_id=by_user_id))
        return await self.quota(org_id)

    async def limit(self, org_id: str) -> SpaceLimit:
        caps = await self.caps()
        own = await self._db.quota(org_id)
        return SpaceLimit(
            per_app=_smaller(caps.max_spaces_per_app, own.max_spaces_per_app if own else None),
            per_org=_smaller(caps.max_spaces_per_org, own.max_spaces if own else None),
        )


class Spaces:
    def __init__(self) -> None:
        self._db = SpacesDB()

    @staticmethod
    def record(org_id: str, row: AppSpace) -> SpaceRecord:
        return SpaceRecord(
            space_id=row.id,
            app_id=row.app_id,
            org_id=org_id,
            external_id=row.external_id,
            producer=ProducerRef(kind=SPACE, id=row.id),
            created_at=row.created_at,
        )

    @staticmethod
    def _own(caller: ProducerCaller, app_id: str) -> str:
        """The app's organisation, when the caller is that app; a 403 otherwise."""
        if caller.kind != APP or caller.id != app_id or not caller.org_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the app itself manages its spaces")
        return caller.org_id

    async def _found(self, org_id: str, app_id: str, external_id: str) -> AppSpace:
        found = await self._db.by_external(org_id, app_id, external_id)
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such space')
        return found

    async def declare(self, caller: ProducerCaller, app_id: str, external_id: str) -> tuple[SpaceRecord, bool]:
        """The app's space for this user, made if new — with a VM credential waiting for the node agent."""
        org_id = self._own(caller, app_id)
        limit = await SpaceLimits().limit(org_id)
        try:
            row, created = await self._db.declare(
                org_id,
                AppSpace(id=str(uuid.uuid4()), app_id=app_id, external_id=external_id),
                per_app=limit.per_app,
                per_org=limit.per_org,
            )
        except SpaceLimitReached as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if created:
            await VmCredentials().issue(
                org_id, ProducerRef(kind=SPACE, id=row.id), by_kind=APP, by_id=app_id, for_pickup=True
            )
        return self.record(org_id, row), created

    async def get(self, caller: ProducerCaller, app_id: str, external_id: str) -> SpaceRecord:
        org_id = self._own(caller, app_id)
        return self.record(org_id, await self._found(org_id, app_id, external_id))

    async def page(self, caller: ProducerCaller, app_id: str, *, after: str | None, limit: int) -> SpacePage:
        org_id = self._own(caller, app_id)
        rows = await self._db.page(org_id, app_id, after=after, limit=limit + 1)
        return SpacePage(
            spaces=[self.record(org_id, row) for row in rows[:limit]],
            next=rows[limit - 1].external_id if len(rows) > limit else None,
        )

    async def reissue(self, caller: ProducerCaller, app_id: str, external_id: str) -> VmCredentialRecord:
        """A new VM credential for the space, waiting for pickup; the old one stops."""
        org_id = self._own(caller, app_id)
        space = await self._found(org_id, app_id, external_id)
        row, _ = await VmCredentials().issue(
            org_id, ProducerRef(kind=SPACE, id=space.id), by_kind=APP, by_id=app_id, for_pickup=True
        )
        return VmCredentials.record(org_id, row)

    async def remove(self, caller: ProducerCaller, app_id: str, external_id: str) -> None:
        org_id = self._own(caller, app_id)
        await self.forget(org_id, [(await self._found(org_id, app_id, external_id)).id])

    async def forget(self, org_id: str, space_ids: list[str]) -> None:
        """These spaces are gone: their credentials stop, their events are dropped, and the feed says so."""
        await VmCredentialsDB().revoke_producers(org_id, SPACE, space_ids, reason=SPACE_REMOVED)
        await ProducerEventsDB().drop_for(org_id, producer_kind=SPACE, producer_ids=space_ids, reason=PRODUCER_REMOVED)
        await self._db.remove(org_id, space_ids)
        # Their marks are in the organisation's database: wake the long polls now.
        await FeedSignals().notify()

    async def forget_app(self, org_id: str, app_id: str) -> None:
        """The app is being deleted: every space of it goes the same way."""
        await self.forget(org_id, await self._db.of_app(org_id, app_id))
