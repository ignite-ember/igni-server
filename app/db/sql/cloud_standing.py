"""A person's standing toward the cloud, in their organisation's database: their group's mode, their events' owner."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.sql.bind_chunks import chunks
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_group_member import OrgGroupMember
from app.db.sql.models.person_event_owner import EventsOwner, PersonEventOwner
from app.db.sql.schemas.cloud_standing import CloudStanding
from app.db.sql.tenancy import TenantData


class CloudStandingDB(TenantData):
    async def standing(self, org_id: str, user_ids: list[str]) -> dict[str, CloudStanding]:
        """Each of these people's group mode and chosen owner, read in one transaction.

        Somebody in no group yet has the Default group's mode, as they get
        its pack and its VM profile. Every id asked for is in the answer.
        """
        found = {user_id: CloudStanding() for user_id in user_ids}
        if not user_ids:
            return found
        async with self.session_scope(org_id) as session:
            default = (
                await session.execute(select(OrgGroup.mode).where(OrgGroup.is_default.is_(True)))
            ).scalar_one_or_none()
            grouped: set[str] = set()
            for part in chunks(user_ids):
                modes = await session.execute(
                    select(OrgGroupMember.user_id, OrgGroup.mode)
                    .join(OrgGroup, OrgGroup.id == OrgGroupMember.group_id)
                    .where(OrgGroupMember.user_id.in_(part))
                )
                for user_id, mode in modes.all():
                    found[str(user_id)].mode = mode
                    grouped.add(str(user_id))
                chosen = await session.execute(
                    select(PersonEventOwner.user_id, PersonEventOwner.owner).where(
                        PersonEventOwner.org_id == org_id, PersonEventOwner.user_id.in_(part)
                    )
                )
                for user_id, owner in chosen.all():
                    found[str(user_id)].chosen = owner
            if default is not None:
                for user_id in set(user_ids) - grouped:
                    found[user_id].mode = default
        return found

    async def choose(self, org_id: str, user_id: str, owner: EventsOwner) -> EventsOwner | None:
        """Record the person's choice; what they had chosen before, if anything."""
        async with self.session_scope(org_id) as session:
            before = (
                await session.execute(
                    select(PersonEventOwner.owner).where(
                        PersonEventOwner.org_id == org_id, PersonEventOwner.user_id == user_id
                    )
                )
            ).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            await session.execute(
                insert(PersonEventOwner)
                .values(org_id=org_id, user_id=user_id, owner=owner, updated_at=now)
                .on_conflict_do_update(constraint='pk_person_event_owner', set_={'owner': owner, 'updated_at': now})
            )
            return before
