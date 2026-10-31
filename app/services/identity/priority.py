"""The super admin's priority order of extensions.

Two extensions that want one thing collide, and the one higher in this
order has it — automatically, on the Services page and on every machine
(`app.services.identity.impact`). It is also the order several
extensions' prompt edits stack in and their hooks are asked in.
A super admin sets it once for the deployment. Unset, it is the order
the extensions' services were approved in; an extension the saved order
does not name comes after every one it does, in that same approval
order — so a newly approved service starts at the bottom. Every machine
receives the effective order with the distribution listing
(`GET /v1/extensions`, `priority`).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.sql.extension_priority import ExtensionPriorityDB, StoredPriority
from app.db.sql.models.registered_client import RegisteredClient
from app.db.sql.service_extensions import ServiceExtensionDB


class PriorityEntry(BaseModel):
    extension_id: str
    name: str
    #: The service that ships it.
    service: str


class PriorityView(BaseModel):
    """The order as the Services page shows it, highest first."""

    entries: list[PriorityEntry] = Field(default_factory=list)
    #: Who set it, when anybody has; empty while it is the approval order.
    set_by: str = ''
    set_at: str | None = None


class PriorityRefused(ValueError):
    """An order that is not exactly the extensions there are."""


class ExtensionPriority:
    def __init__(self, store: ExtensionPriorityDB | None = None, extensions: ServiceExtensionDB | None = None) -> None:
        self._store = store or ExtensionPriorityDB()
        self._extensions = extensions or ServiceExtensionDB()

    @staticmethod
    def effective(saved: list[str], approved: list[tuple[str, datetime | None]]) -> list[str]:
        """`approved` as `(extension_id, approved_at)`, put in priority order, highest first."""
        known = {extension_id for extension_id, _ in approved}
        first = [extension_id for extension_id in saved if extension_id in known]
        rest = sorted(
            (pair for pair in approved if pair[0] not in set(first)),
            key=lambda pair: (pair[1] is None, pair[1] or datetime.max, pair[0]),
        )
        return first + [extension_id for extension_id, _ in rest]

    @staticmethod
    def _approved_at(client: RegisteredClient) -> datetime | None:
        """When its service was approved — or registered, for one approved without a recorded decision."""
        return client.decided_at or client.created_at

    async def distributed(self) -> list[str]:
        """The order for what machines receive."""
        approved = [
            (ext.extension_id, self._approved_at(client)) for ext, client in await self._extensions.distributed()
        ]
        return self.effective((await self._store.saved()).order, approved)

    async def reviewable(self) -> list[str]:
        """The order over every extension a super admin can place — distributed or pending approval."""
        saved = await self._store.saved()
        rows = await self._extensions.reviewable_with_clients()
        return self.effective(saved.order, [(ext.extension_id, self._approved_at(client)) for ext, client in rows])

    async def view(self) -> PriorityView:
        """Every extension a super admin can place — distributed or pending approval — in order."""
        saved = await self._store.saved()
        rows = await self._extensions.reviewable_with_clients()
        approved = [(ext.extension_id, self._approved_at(client)) for ext, client in rows]
        by_id = {ext.extension_id: (ext, client) for ext, client in rows}
        return PriorityView(
            entries=[
                PriorityEntry(
                    extension_id=extension_id,
                    name=str(by_id[extension_id][0].manifest.get('name') or extension_id),
                    service=by_id[extension_id][1].name,
                )
                for extension_id in self.effective(saved.order, approved)
            ],
            set_by=saved.set_by,
            set_at=saved.set_at.isoformat() if saved.set_at else None,
        )

    async def set(self, order: list[str], *, set_by_user_id: str | None) -> StoredPriority:
        """Save the whole order. It names every extension there is, each once."""
        there = {ext.extension_id for ext, _ in await self._extensions.reviewable_with_clients()}
        if len(order) != len(set(order)) or set(order) != there:
            raise PriorityRefused('the order names every extension there is, each once: ' + ', '.join(sorted(there)))
        return await self._store.replace(order, set_by_user_id=set_by_user_id)


__all__ = ['ExtensionPriority', 'PriorityEntry', 'PriorityRefused', 'PriorityView']
