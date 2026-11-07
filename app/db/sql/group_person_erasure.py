"""Clearing a deleted account out of one organisation's groups."""

from __future__ import annotations

from app.db.sql.group_transaction import GroupAggregate


class GroupPersonErasureDB(GroupAggregate):
    """What the foreign keys to `users` used to do for an organisation's groups."""

    async def forget_person(self, org_id: str, *, user_id: str) -> None:
        """A person's account is gone: what the foreign keys to `users` used to do.

        Groups are in the organisation's own database and `users` is in the
        shared plane, so nothing cascades between them any more. Their
        group membership goes; where they appear as the one who created
        or added something, the record stays and the name is cleared.
        """
        async with self._transaction(org_id) as tx:
            await tx.forget_member(user_id=user_id)
            await tx.forget_creator(user_id=user_id)
            await tx.forget_contributor(user_id=user_id)
