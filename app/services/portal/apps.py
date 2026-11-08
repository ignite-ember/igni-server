"""An organisation's apps: registering them, renaming, rotating keys, removing.

An app is the cloud's other kind of producer (`docs/service-identity.md`
§11). An org admin registers it here with a name, an owner and the app's
public keys; it is then a registered client of kind `app` in that
organisation, approved by that act, and obtains `app` tokens as itself
with `client_credentials` and `private_key_jwt` — the mechanism services
use. It is in no group and acts only as itself. Discarding one is
`AppDiscard`; its extensions are `AppExtensions`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.api.base_v1.schema.portal.apps import (
    OrgAppList,
    OrgAppResponse,
    RegisterAppRequest,
    ReplaceAppKeysRequest,
    UpdateAppRequest,
)
from app.db.sql.apps import AppsDB
from app.db.sql.models.org_app import OrgApp
from app.db.sql.models.registered_client import APP, APPROVED, RegisteredClient
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.team import TeamDB
from app.db.sql.vm_profile_requests import VmProfileRequestsDB
from app.services.cloud.spaces import Spaces
from app.services.cloud.vm_credentials import VmCredentials
from app.services.identity.jwk import JwkSet
from app.services.portal.app_errors import AppKeyTaken, AppNotFound, OwnerNotInOrg
from app.services.portal.app_views import AppViews


class OrgApps:
    """The apps of one organisation, as its admins manage them. Raises the errors in `app_errors`."""

    def __init__(
        self,
        *,
        clients: RegisteredClientDB | None = None,
        apps: AppsDB | None = None,
        team: TeamDB | None = None,
        views: AppViews | None = None,
        credentials: VmCredentials | None = None,
        spaces: Spaces | None = None,
        profile_requests: VmProfileRequestsDB | None = None,
    ) -> None:
        self._clients = clients or RegisteredClientDB()
        self._apps = apps or AppsDB()
        self._team = team or TeamDB()
        self._views = views or AppViews(apps=self._apps)
        self._credentials = credentials or VmCredentials()
        self._spaces = spaces or Spaces()
        self._profile_requests = profile_requests or VmProfileRequestsDB()

    async def is_member(self, org_id: str, user_id: str) -> bool:
        """Whether *user_id* is an active member of the organisation, as an app's owner must be."""
        return await self._team.get_active_member(org_id, user_id) is not None

    async def _owner_in_org(self, org_id: str, user_id: str) -> None:
        if not await self.is_member(org_id, user_id):
            raise OwnerNotInOrg()

    async def _key_is_free(self, keys: JwkSet, *, but: str | None = None) -> str:
        thumbprint = keys.keys[0].thumbprint()
        holder = await self._clients.by_thumbprint(thumbprint)
        if holder is not None and holder.client_id != but:
            raise AppKeyTaken()
        return thumbprint

    async def client(self, org_id: str, app_id: str) -> RegisteredClient:
        """The app's registered client; `AppNotFound` also for an app of another organisation."""
        client = await self._clients.app(org_id, app_id)
        if client is None or await self._apps.get(org_id, app_id) is None:
            raise AppNotFound()
        return client

    async def register(self, org_id: str, *, body: RegisterAppRequest, by_user_id: str) -> OrgAppResponse:
        keys = body.jwks.public_keys()
        await self._owner_in_org(org_id, body.owner_user_id)
        thumbprint = await self._key_is_free(keys)
        client = RegisteredClient(
            kind=APP,
            org_id=org_id,
            name=body.name.strip(),
            base_url='',
            redirect_uris=[],
            jwks=keys.model_dump(exclude_none=True),
            thumbprint=thumbprint,
            # The org admin registering it is the decision: an app acts
            # only in their organisation.
            state=APPROVED,
            decided_at=datetime.now(timezone.utc),
            decided_by_user_id=by_user_id,
        )
        stored = await self._clients.add(client)
        if stored.client_id != client.client_id:
            # Registered by another client between the check and the add.
            raise AppKeyTaken(raced=True)
        await self._apps.add(
            org_id, OrgApp(app_id=client.client_id, owner_user_id=body.owner_user_id, created_by_user_id=by_user_id)
        )
        return await self._views.one(org_id, client)

    async def list(self, org_id: str) -> OrgAppList:
        return OrgAppList(apps=await self._views.all(org_id, await self._clients.apps_of(org_id)))

    async def get(self, org_id: str, app_id: str) -> OrgAppResponse:
        return await self._views.one(org_id, await self.client(org_id, app_id))

    async def update(self, org_id: str, app_id: str, *, body: UpdateAppRequest) -> OrgAppResponse:
        await self.client(org_id, app_id)
        if body.owner_user_id is not None:
            await self._owner_in_org(org_id, body.owner_user_id)
            await self._apps.set_owner(org_id, app_id, body.owner_user_id)
        if body.name is not None:
            await self._clients.rename(app_id, body.name.strip())
        return await self.get(org_id, app_id)

    async def replace_keys(self, org_id: str, app_id: str, *, body: ReplaceAppKeysRequest) -> OrgAppResponse:
        await self.client(org_id, app_id)
        keys = body.jwks.public_keys()
        thumbprint = await self._key_is_free(keys, but=app_id)
        await self._clients.replace_keys(app_id, jwks=keys.model_dump(exclude_none=True), thumbprint=thumbprint)
        return await self.get(org_id, app_id)

    async def remove(self, org_id: str, app_id: str) -> None:
        """Forget the app: its token requests stop at once, and its tokens expire within minutes."""
        await self.client(org_id, app_id)
        await self._credentials.revoke_app(org_id, app_id)
        await self._spaces.forget_app(org_id, app_id)
        await self._profile_requests.remove(org_id, APP, app_id)
        await self._apps.remove(org_id, app_id)
        await self._clients.remove(app_id)
