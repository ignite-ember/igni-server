"""A super admin's review of registered clients: what is waiting, and the decision on it.

Approving lets a client obtain tokens for any organisation on the
install. An approved service's newer version that asks for more is held
(`app.services.identity.extensions.HeldUpdates`) and decided here too.
Each lookup and decision answers with what the portal shows about the
client afterwards, or raises a typed refusal the router maps to HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.sql.models.registered_client import RegisteredClient
from app.db.sql.models.service_extension import ServiceExtension
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.service_extensions import ServiceExtensionDB
from app.services.identity.extension_review import ExtensionReview, ExtensionReviews
from app.services.identity.extensions import HeldUpdate, HeldUpdates
from app.services.identity.registry import ClientRegistry


class ClientNotFound(LookupError):
    """No client is registered under this id."""


class NoHeldUpdate(LookupError):
    """No client under this id, or it has no version waiting for a super admin."""


@dataclass(frozen=True)
class ReviewedClient:
    """A registered client with its live extension and its held one, if any."""

    client: RegisteredClient
    extension: ServiceExtension | None = None
    held: ServiceExtension | None = None


@dataclass(frozen=True)
class ClientDossier:
    """Everything the portal shows about one client."""

    reviewed: ReviewedClient
    #: The live extension's impact and collisions; `None` without one.
    review: ExtensionReview | None = None
    #: The held version and what it adds, if one is waiting.
    held_update: HeldUpdate | None = None


@dataclass(frozen=True)
class HeldDecision:
    """A held version decided: the client as it is now, and the version that was decided."""

    reviewed: ReviewedClient
    decided: HeldUpdate


class ClientReview:
    def __init__(
        self,
        *,
        registry: ClientRegistry | None = None,
        clients: RegisteredClientDB | None = None,
        store: ServiceExtensionDB | None = None,
        held_updates: HeldUpdates | None = None,
        reviews: ExtensionReviews | None = None,
    ) -> None:
        self._clients = clients or RegisteredClientDB()
        self._registry = registry or ClientRegistry(store=self._clients)
        self._store = store or ServiceExtensionDB()
        self._held_updates = held_updates or HeldUpdates(store=self._store, clients=self._clients)
        self._reviews = reviews or ExtensionReviews(store=self._store)

    async def all(self) -> list[ReviewedClient]:
        extensions = await self._store.all_described()
        held = await self._store.all_held()
        return [
            ReviewedClient(client=client, extension=extensions.get(client.client_id), held=held.get(client.client_id))
            for client in await self._registry.all()
        ]

    async def held_updates(self) -> list[HeldUpdate]:
        """Every held version, oldest first, each with what it asks for that the approved one did not."""
        return await self._held_updates.all()

    async def dossier(self, client_id: str) -> ClientDossier:
        client = await self._clients.get(client_id)
        if client is None:
            raise ClientNotFound(client_id)
        reviewed = await self._reviewed(client)
        return ClientDossier(
            reviewed=reviewed,
            review=await self._reviews.review(client_id) if reviewed.extension is not None else None,
            held_update=await self._held_updates.of(client_id),
        )

    async def approve(self, client_id: str, *, by_user_id: str | None) -> ReviewedClient:
        """Raises `ExtensionIdTaken` when another approved service already ships its extension."""
        return await self._decided(await self._registry.approve(client_id, by_user_id=by_user_id), client_id)

    async def revoke(self, client_id: str, *, by_user_id: str | None) -> ReviewedClient:
        return await self._decided(await self._registry.revoke(client_id, by_user_id=by_user_id), client_id)

    async def remove(self, client_id: str) -> None:
        """Forget a registration; the client can register again with the same key, and starts over as pending."""
        if not await self._registry.remove(client_id):
            raise ClientNotFound(client_id)

    async def approve_held(self, client_id: str) -> HeldDecision:
        """The held version becomes the approved one. Raises `ExtensionConflict` when another service ships its id."""
        client, held = await self._waiting(client_id)
        await self._held_updates.approve(client_id)
        return HeldDecision(reviewed=await self._reviewed(client), decided=held)

    async def reject_held(self, client_id: str) -> HeldDecision:
        """The held version is discarded; the service stays on the approved one."""
        client, held = await self._waiting(client_id)
        await self._held_updates.reject(client_id)
        return HeldDecision(reviewed=await self._reviewed(client), decided=held)

    async def _waiting(self, client_id: str) -> tuple[RegisteredClient, HeldUpdate]:
        client = await self._clients.get(client_id)
        held = await self._held_updates.of(client_id)
        if client is None or held is None:
            raise NoHeldUpdate(client_id)
        return client, held

    async def _decided(self, client: RegisteredClient | None, client_id: str) -> ReviewedClient:
        if client is None:
            raise ClientNotFound(client_id)
        return await self._reviewed(client)

    async def _reviewed(self, client: RegisteredClient) -> ReviewedClient:
        return ReviewedClient(
            client=client,
            extension=await self._store.of(client.client_id),
            held=await self._store.held_of(client.client_id),
        )


def client_review() -> ClientReview:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return ClientReview()
