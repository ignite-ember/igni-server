"""Legal holds: an org admin keeps a removed member's VMs from being erased (`docs/service-identity.md` §18.2).

A hold is on a person in one organisation and covers every removed
incarnation of theirs there not yet erased — and any removed while it
stands. While it stands their `erase_at` is null; releasing it
reschedules each to its removal plus the retention it was removed under,
or now if that has passed. Placing and releasing are audited (portal
routes), and each erasure moved is in the trail too. A personal erasure
request is refused while a hold stands.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.api.base_v1.schema.retention import HeldIncarnation, LegalHoldList, LegalHoldResponse
from app.db.sql.models.retention import LegalHold
from app.db.sql.producer_feed import ProducerFeedDB
from app.db.sql.retention import RetentionDB
from app.security.org_authority import OrgAuthority
from app.services.cloud.producer_feed import ProducerFeed


class LegalHolds:
    def __init__(self) -> None:
        self._db = RetentionDB()

    async def response(self, hold: LegalHold) -> LegalHoldResponse:
        return LegalHoldResponse(
            org_id=hold.org_id,
            user_id=hold.user_id,
            reason=hold.reason,
            placed_by_user_id=hold.placed_by_user_id,
            placed_at=hold.placed_at,
            incarnations=[
                HeldIncarnation(incarnation=row.incarnation, removed_at=row.removed_at, erase_at=row.erase_at)
                for row in await ProducerFeedDB().removed_incarnations(hold.org_id, hold.user_id)
            ],
        )

    async def list(self, org_id: str) -> LegalHoldList:
        return LegalHoldList(holds=[await self.response(hold) for hold in await self._db.holds(org_id)])

    async def place(self, authority: OrgAuthority, user_id: str, reason: str) -> LegalHoldResponse:
        await self._db.put_hold(
            LegalHold(org_id=authority.org_id, user_id=user_id, reason=reason, placed_by_user_id=authority.user_id)
        )
        await ProducerFeed().reschedule(authority.org_id, user_id, held=True)
        hold = await self._db.hold(authority.org_id, user_id)
        assert hold is not None
        return await self.response(hold)

    async def release(self, authority: OrgAuthority, user_id: str) -> None:
        if not await self._db.release(authority.org_id, user_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No legal hold on this person here')
        await ProducerFeed().reschedule(authority.org_id, user_id, held=False)

    async def refuse_erasure_if_held(self, user_id: str) -> None:
        """A personal erasure request overrides retention — but not a legal hold."""
        held_in = await self._db.holding(user_id)
        if held_in:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    'This person is under a legal hold in organisation(s) '
                    f'{", ".join(sorted(held_in))}; nothing of theirs can be erased until it is released'
                ),
            )
