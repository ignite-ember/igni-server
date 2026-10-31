"""Storing what services are still owed about deleted organisations.

See `app.db.sql.models.organisation_deletion_notice`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import delete, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.organisation_deletion_notice import OrganisationDeletionNotice
from app.db.sql.models.registered_client import APPROVED, RegisteredClient, SERVICE
from app.utils.singleton import SingletonMeta


class DueNotice(BaseModel):
    """One service to tell about one deleted organisation, now."""

    org_id: str
    client_id: str
    service_name: str
    base_url: str
    attempts: int


class OrganisationDeletionNoticeDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    @staticmethod
    async def owe(session: AsyncSession, org_ids: list[str], *, now: datetime) -> None:
        """Owe every approved service the news that `org_ids` are deleted.

        Takes the caller's session on purpose: written in the transaction
        that deletes the organisations, so a deletion that commits is a
        deletion every service will hear about, and one that rolls back
        tells nobody anything.
        """
        if not org_ids:
            return
        for org_id in org_ids:
            services = select(
                literal(org_id),
                RegisteredClient.client_id,
                literal(now),
                literal(0),
                literal(now),
                literal(''),
            ).where(
                RegisteredClient.state == APPROVED,
                RegisteredClient.kind == SERVICE,
                RegisteredClient.base_url != '',
            )
            await session.execute(
                insert(OrganisationDeletionNotice)
                .from_select(
                    ['org_id', 'client_id', 'created_at', 'attempts', 'next_attempt_at', 'last_error'], services
                )
                .on_conflict_do_nothing(index_elements=['org_id', 'client_id'])
            )

    async def take_due(self, *, now: datetime, lease: timedelta, limit: int) -> list[DueNotice]:
        """Notices due now, to approved services, each leased to the caller.

        `FOR UPDATE SKIP LOCKED` and the lease together: two replicas never
        take the same row, and a replica that dies holding one lets it go
        when `lease` runs out. A service no longer approved is skipped,
        not forgotten — it is told if it is approved again, and its notices
        go when its registration does.
        """
        async with self.session_scope() as session:
            rows = (
                await session.execute(
                    select(OrganisationDeletionNotice, RegisteredClient.name, RegisteredClient.base_url)
                    .join(RegisteredClient, RegisteredClient.client_id == OrganisationDeletionNotice.client_id)
                    .where(
                        OrganisationDeletionNotice.next_attempt_at <= now,
                        RegisteredClient.state == APPROVED,
                        RegisteredClient.base_url != '',
                    )
                    .order_by(OrganisationDeletionNotice.next_attempt_at)
                    .limit(limit)
                    .with_for_update(of=OrganisationDeletionNotice, skip_locked=True)
                )
            ).all()
            due: list[DueNotice] = []
            for notice, name, base_url in rows:
                notice.next_attempt_at = now + lease
                due.append(
                    DueNotice(
                        org_id=notice.org_id,
                        client_id=notice.client_id,
                        service_name=name,
                        base_url=base_url,
                        attempts=notice.attempts,
                    )
                )
            return due

    async def confirmed(self, org_id: str, client_id: str) -> None:
        async with self.session_scope() as session:
            await session.execute(
                delete(OrganisationDeletionNotice).where(
                    OrganisationDeletionNotice.org_id == org_id, OrganisationDeletionNotice.client_id == client_id
                )
            )

    async def unconfirmed(self, org_id: str, client_id: str, *, retry_at: datetime, error: str) -> None:
        async with self.session_scope() as session:
            await session.execute(
                update(OrganisationDeletionNotice)
                .where(OrganisationDeletionNotice.org_id == org_id, OrganisationDeletionNotice.client_id == client_id)
                .values(
                    attempts=OrganisationDeletionNotice.attempts + 1,
                    next_attempt_at=retry_at,
                    last_error=error[:500],
                )
            )

    async def outstanding(self, org_id: str | None = None) -> list[OrganisationDeletionNotice]:
        """Every notice not yet confirmed; one organisation's, given one."""
        async with self.session_scope() as session:
            query = select(OrganisationDeletionNotice).order_by(OrganisationDeletionNotice.created_at)
            if org_id is not None:
                query = query.where(OrganisationDeletionNotice.org_id == org_id)
            return list((await session.execute(query)).scalars().all())
