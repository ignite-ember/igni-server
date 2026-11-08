"""A super admin's review of held extension versions: list, approve, reject.

An approved service's upload that asks for more than the approved version
is held (`app.services.identity.extensions.ServiceExtensions`): machines
keep receiving the approved one until a super admin decides on it here,
on the Services page.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.db.sql.models.service_extension import ServiceExtension
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.service_extensions import ServiceExtensionDB
from app.services.identity.extension_errors import ExtensionConflict
from app.services.identity.extension_grants import ExtensionGrants
from app.services.identity.extensions import ServiceExtensions
from app.services.identity.grants_comparison import GrantsComparison


class HeldUpdate(BaseModel):
    """An approved service's upload that asks for more, waiting for a super admin."""

    client_id: str
    #: The service waiting.
    service: str
    extension_id: str
    name: str
    #: The version machines receive now.
    approved_version: str | None
    #: The version waiting.
    version: str
    sha256: str
    uploaded_at: datetime
    #: What it asks for that the approved version did not.
    comparison: GrantsComparison


class HeldUpdates:
    """A super admin's review of held versions: list, approve, reject."""

    def __init__(
        self,
        store: ServiceExtensionDB | None = None,
        clients: RegisteredClientDB | None = None,
        extensions: ServiceExtensions | None = None,
    ) -> None:
        self._store = store or ServiceExtensionDB()
        self._clients = clients or RegisteredClientDB()
        self._extensions = extensions or ServiceExtensions(store=self._store, clients=self._clients)

    async def all(self) -> list[HeldUpdate]:
        held = await self._store.all_held()
        updates = [await self._describe(row) for row in held.values()]
        return sorted((u for u in updates if u is not None), key=lambda u: u.uploaded_at)

    async def of(self, client_id: str) -> HeldUpdate | None:
        row = await self._store.held_of(client_id)
        return await self._describe(row) if row is not None else None

    async def _describe(self, row: ServiceExtension) -> HeldUpdate | None:
        client = await self._clients.get(row.client_id)
        if client is None:
            return None
        live = await self._store.of(row.client_id)
        comparison = GrantsComparison.between(ExtensionGrants.of_version(live), ExtensionGrants.of_version(row))
        return HeldUpdate(
            client_id=row.client_id,
            service=client.name,
            extension_id=row.extension_id,
            name=str(row.manifest.get('name') or row.extension_id),
            approved_version=live.version if live is not None else None,
            version=row.version,
            sha256=row.sha256,
            uploaded_at=row.published_at,
            comparison=comparison,
        )

    async def approve(self, client_id: str) -> ServiceExtension | None:
        """The held version becomes the approved one, and is distributed. `None` when nothing is held."""
        held = await self._store.held_of(client_id)
        if held is None:
            return None
        owner = await self._store.approved_owner_of(held.extension_id)
        if owner is not None and owner != client_id:
            raise ExtensionConflict(f'another approved service already ships {held.extension_id}')
        promoted = await self._store.promote(client_id)
        await self._extensions.keep_only_declared(client_id)
        return promoted

    async def reject(self, client_id: str) -> bool:
        """Discard the held version; the service stays on the approved one. False when nothing is held."""
        discarded = await self._store.discard_held(client_id)
        if discarded:
            await self._extensions.keep_only_declared(client_id)
        return discarded
