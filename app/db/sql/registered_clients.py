"""Storing registered clients. See `app.db.sql.models.registered_client`."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.registered_client import APP, APPROVED, PENDING, RegisteredClient
from app.utils.singleton import SingletonMeta


class RegisteredClientDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def get(self, client_id: str) -> RegisteredClient | None:
        async with self.session_scope() as session:
            return await session.get(RegisteredClient, client_id)

    async def by_thumbprint(self, thumbprint: str) -> RegisteredClient | None:
        async with self.session_scope() as session:
            result = await session.execute(select(RegisteredClient).where(RegisteredClient.thumbprint == thumbprint))
            return result.scalars().first()

    async def add(self, client: RegisteredClient) -> RegisteredClient:
        """Store a registration; the existing one if its key got there first.

        Two registrations with one key arrive together when a service
        retries before its first attempt answered. The thumbprint index
        lets one in, and the other is answered with it — the same thing a
        later re-registration gets.
        """
        try:
            async with self.session_scope() as session:
                session.add(client)
        except IntegrityError:
            existing = await self.by_thumbprint(client.thumbprint)
            if existing is None:
                raise
            return existing
        return client

    async def all(self) -> list[RegisteredClient]:
        """Every service and platform client — the deployment's. An app is its organisation's."""
        async with self.session_scope() as session:
            result = await session.execute(
                select(RegisteredClient).where(RegisteredClient.kind != APP).order_by(RegisteredClient.created_at)
            )
            return list(result.scalars().all())

    async def apps_of(self, org_id: str) -> list[RegisteredClient]:
        async with self.session_scope() as session:
            result = await session.execute(
                select(RegisteredClient)
                .where(RegisteredClient.kind == APP, RegisteredClient.org_id == org_id)
                .order_by(RegisteredClient.created_at)
            )
            return list(result.scalars().all())

    async def app(self, org_id: str, app_id: str) -> RegisteredClient | None:
        """An app of this organisation, and nothing else by that id."""
        client = await self.get(app_id)
        return client if client is not None and client.kind == APP and client.org_id == org_id else None

    async def rename(self, client_id: str, name: str) -> None:
        async with self.session_scope() as session:
            client = await session.get(RegisteredClient, client_id)
            if client is not None:
                client.name = name

    async def replace_keys(self, client_id: str, *, jwks: dict[str, object], thumbprint: str) -> None:
        """New keys; the thumbprint index refuses a key another client holds (IntegrityError)."""
        async with self.session_scope() as session:
            client = await session.get(RegisteredClient, client_id)
            if client is not None:
                client.jwks = jwks
                client.thumbprint = thumbprint

    async def approved_services(self) -> list[RegisteredClient]:
        """Every approved service with somewhere to reach it."""
        async with self.session_scope() as session:
            result = await session.execute(
                select(RegisteredClient)
                .where(RegisteredClient.state == APPROVED, RegisteredClient.kind == 'service')
                .where(RegisteredClient.base_url != '')
                .order_by(RegisteredClient.created_at)
            )
            return list(result.scalars().all())

    async def pending_count(self) -> int:
        async with self.session_scope() as session:
            result = await session.execute(
                select(func.count()).select_from(RegisteredClient).where(RegisteredClient.state == PENDING)
            )
            return int(result.scalar() or 0)

    async def decide(self, client_id: str, *, state: str, by_user_id: str | None) -> RegisteredClient | None:
        async with self.session_scope() as session:
            client = await session.get(RegisteredClient, client_id)
            if client is None:
                return None
            client.state = state
            client.decided_at = datetime.now(timezone.utc)
            client.decided_by_user_id = by_user_id
            return client

    async def remove(self, client_id: str) -> bool:
        async with self.session_scope() as session:
            client = await session.get(RegisteredClient, client_id)
            if client is None:
                return False
            await session.delete(client)
            return True
