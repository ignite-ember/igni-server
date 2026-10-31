"""Storing service extensions. See `app.db.sql.models.service_extension`.

Everything here that describes or distributes an extension reads the
`live` stage only. A `held` upload is reached through the methods that
name it — `held_of`, `all_held`, `promote`, `discard_held` — so a held
version cannot reach a machine, or stand in for the approved one, by a
query that forgot to ask.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import defer

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.registered_client import APPROVED, RegisteredClient, REVOKED
from app.db.sql.models.service_extension import HELD, LIVE, ServiceExtension
from app.utils.singleton import SingletonMeta


class ServiceExtensionDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def put(self, extension: ServiceExtension) -> ServiceExtension:
        """Store an upload at its stage, replacing whatever the client had there.

        A new `live` version also discards a `held` one: the service has
        moved on from it.
        """
        extension.stage = extension.stage or LIVE
        extension.published_at = datetime.now(timezone.utc)
        async with self.session_scope() as session:
            if extension.stage == LIVE:
                await session.execute(
                    delete(ServiceExtension).where(
                        ServiceExtension.client_id == extension.client_id, ServiceExtension.stage == HELD
                    )
                )
            await session.merge(extension)
        return extension

    async def of(self, client_id: str) -> ServiceExtension | None:
        """The client's live upload, without its archive."""
        return await self._at(client_id, LIVE)

    async def held_of(self, client_id: str) -> ServiceExtension | None:
        """The client's held upload, without its archive."""
        return await self._at(client_id, HELD)

    async def _at(self, client_id: str, stage: str) -> ServiceExtension | None:
        async with self.session_scope() as session:
            result = await session.execute(
                select(ServiceExtension)
                .options(defer(ServiceExtension.archive))
                .where(ServiceExtension.client_id == client_id, ServiceExtension.stage == stage)
            )
            return result.scalars().first()

    async def all_held(self) -> dict[str, ServiceExtension]:
        """Every held upload, without archives, by client id."""
        async with self.session_scope() as session:
            result = await session.execute(
                select(ServiceExtension).options(defer(ServiceExtension.archive)).where(ServiceExtension.stage == HELD)
            )
            return {row.client_id: row for row in result.scalars().all()}

    async def promote(self, client_id: str) -> ServiceExtension | None:
        """The held upload becomes live, replacing the live one. `None` when nothing is held."""
        async with self.session_scope() as session:
            held = (
                await session.execute(
                    select(ServiceExtension.client_id).where(
                        ServiceExtension.client_id == client_id, ServiceExtension.stage == HELD
                    )
                )
            ).first()
            if held is None:
                return None
            await session.execute(
                delete(ServiceExtension).where(ServiceExtension.client_id == client_id, ServiceExtension.stage == LIVE)
            )
            await session.execute(
                update(ServiceExtension)
                .where(ServiceExtension.client_id == client_id, ServiceExtension.stage == HELD)
                .values(stage=LIVE)
            )
        return await self.of(client_id)

    async def discard_held(self, client_id: str) -> bool:
        """Forget the held upload. False when there was none."""
        async with self.session_scope() as session:
            result = await session.execute(
                delete(ServiceExtension).where(ServiceExtension.client_id == client_id, ServiceExtension.stage == HELD)
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def all_described(self) -> dict[str, ServiceExtension]:
        """Every client's live upload, without archives, by client id."""
        async with self.session_scope() as session:
            result = await session.execute(
                select(ServiceExtension).options(defer(ServiceExtension.archive)).where(ServiceExtension.stage == LIVE)
            )
            return {row.client_id: row for row in result.scalars().all()}

    async def reviewable(self) -> list[ServiceExtension]:
        """Each service's live upload, without archives — every service not revoked."""
        return [row for row, _ in await self.reviewable_with_clients()]

    async def reviewable_with_clients(self) -> list[tuple[ServiceExtension, RegisteredClient]]:
        """Each service's live upload with its service, without archives — every service not revoked."""
        async with self.session_scope() as session:
            result = await session.execute(
                select(ServiceExtension, RegisteredClient)
                .options(defer(ServiceExtension.archive))
                .join(RegisteredClient, RegisteredClient.client_id == ServiceExtension.client_id)
                .where(RegisteredClient.state != REVOKED, ServiceExtension.stage == LIVE)
                .order_by(ServiceExtension.extension_id)
            )
            return [(row[0], row[1]) for row in result.all()]

    async def approved_owner_of(self, extension_id: str) -> str | None:
        async with self.session_scope() as session:
            result = await session.execute(
                select(ServiceExtension.client_id)
                .join(RegisteredClient, RegisteredClient.client_id == ServiceExtension.client_id)
                .where(
                    ServiceExtension.extension_id == extension_id,
                    ServiceExtension.stage == LIVE,
                    RegisteredClient.state == APPROVED,
                )
            )
            return result.scalars().first()

    async def distributed(self) -> list[tuple[ServiceExtension, RegisteredClient]]:
        """What machines receive: approved services' live extensions, without archives."""
        async with self.session_scope() as session:
            result = await session.execute(
                select(ServiceExtension, RegisteredClient)
                .options(defer(ServiceExtension.archive))
                .join(RegisteredClient, RegisteredClient.client_id == ServiceExtension.client_id)
                .where(RegisteredClient.state == APPROVED, ServiceExtension.stage == LIVE)
                .order_by(ServiceExtension.extension_id)
            )
            return [(row[0], row[1]) for row in result.all()]

    async def archive_of(self, extension_id: str) -> ServiceExtension | None:
        """An approved service's live extension, with its archive."""
        async with self.session_scope() as session:
            result = await session.execute(
                select(ServiceExtension)
                .join(RegisteredClient, RegisteredClient.client_id == ServiceExtension.client_id)
                .where(
                    ServiceExtension.extension_id == extension_id,
                    ServiceExtension.stage == LIVE,
                    RegisteredClient.state == APPROVED,
                )
            )
            return result.scalars().first()
