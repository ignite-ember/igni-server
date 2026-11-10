"""An app as its organisation's admins see it: its registered client, its row and its owner's address."""

from __future__ import annotations

from app.api.base_v1.schema.portal.apps import OrgAppResponse
from app.db.sql.apps import AppsDB
from app.db.sql.models.org_app import OrgApp
from app.db.sql.models.registered_client import RegisteredClient
from app.db.sql.models.user import User
from app.db.sql.user import UserDB


class AppViews:
    """Builds `OrgAppResponse`s, reading every owner of a list in one query."""

    def __init__(self, *, apps: AppsDB | None = None, users: UserDB | None = None) -> None:
        self._apps = apps or AppsDB()
        self._users = users or UserDB()

    async def one(self, org_id: str, client: RegisteredClient) -> OrgAppResponse:
        app = await self._apps.get(org_id, client.client_id)
        owners = await self._owners([app] if app else [])
        return self._view(client, app, owners)

    async def all(self, org_id: str, clients: list[RegisteredClient]) -> list[OrgAppResponse]:
        apps = {app.app_id: app for app in await self._apps.all(org_id)}
        owners = await self._owners(list(apps.values()))
        return [self._view(client, apps.get(client.client_id), owners) for client in clients]

    async def _owners(self, apps: list[OrgApp]) -> dict[str, User]:
        user_ids = sorted({app.owner_user_id for app in apps if app.owner_user_id})
        return {user.user_id: user for user in await self._users.by_user_ids(user_ids)}

    @staticmethod
    def _view(client: RegisteredClient, app: OrgApp | None, owners: dict[str, User]) -> OrgAppResponse:
        owner = owners.get(app.owner_user_id) if app and app.owner_user_id else None
        return OrgAppResponse(
            app_id=client.client_id,
            client_id=client.client_id,
            name=client.name,
            owner_user_id=app.owner_user_id if app else None,
            owner_email=str(owner.email) if owner and owner.email else None,
            thumbprint=client.thumbprint,
            vm_profile_id=app.vm_profile_id if app else None,
            created_at=client.created_at,
        )
