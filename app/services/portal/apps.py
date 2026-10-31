"""An organisation's apps: registering them, and which extensions each gets.

An app is the cloud's other kind of producer (`docs/service-identity.md`
§11). An org admin registers it here with a name, an owner and the app's
public keys; it is then a registered client of kind `app` in that
organisation, approved by that act, and obtains `app` tokens as itself
with `client_credentials` and `private_key_jwt` — the mechanism services
use. It is in no group and acts only as itself. Its extensions are a
list of its own, off until an org admin switches them on, exactly like a
group's.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.api.base_v1.schema.portal.apps import (
    OrgAppList,
    OrgAppResponse,
    RegisterAppRequest,
    ReplaceAppKeysRequest,
    UpdateAppRequest,
)
from app.api.base_v1.schema.portal.groups import GroupExtension, GroupExtensionList
from app.db.sql.apps import AppExtensionsDB, AppsDB
from app.db.sql.models.org_app import OrgApp
from app.db.sql.models.registered_client import APP, APPROVED, RegisteredClient
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.service_extensions import ServiceExtensionDB
from app.db.sql.team import TeamDB
from app.db.sql.user import UserDB
from app.services.cloud.spaces import Spaces
from app.services.cloud.vm_credentials import VmCredentials
from app.services.identity.jwk import PublicJwk
from app.services.identity.registry import MAX_KEYS


class AppKeys:
    """The public keys an app sends, checked as a service's are."""

    @staticmethod
    def of(jwks: dict[str, list[dict[str, object]]]) -> list[PublicJwk]:
        raw = jwks.get('keys') or []
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail='jwks must hold at least one public key'
            )
        if len(raw) > MAX_KEYS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'At most {MAX_KEYS} keys per app')
        try:
            return [PublicJwk.from_mapping(dict(key)) for key in raw]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


class OrgApps:
    """The apps of one organisation, as its admins manage them."""

    def __init__(self) -> None:
        self._clients = RegisteredClientDB()
        self._apps = AppsDB()
        self._extensions = AppExtensionsDB()
        self._shipped = ServiceExtensionDB()

    async def _owner_in_org(self, org_id: str, user_id: str) -> None:
        if await TeamDB().get_active_member(org_id, user_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail='The owner must be an active member of the organisation'
            )

    async def _key_is_free(self, keys: list[PublicJwk], *, but: str | None = None) -> str:
        thumbprint = keys[0].thumbprint()
        holder = await self._clients.by_thumbprint(thumbprint)
        if holder is not None and holder.client_id != but:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='Another client already registered this key. Each app generates a key pair of its own.',
            )
        return thumbprint

    async def client(self, org_id: str, app_id: str) -> RegisteredClient:
        """The app's registered client, or 404 — also for an app of another organisation."""
        client = await self._clients.app(org_id, app_id)
        if client is None or await self._apps.get(org_id, app_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='App not found')
        return client

    async def response(self, org_id: str, client: RegisteredClient) -> OrgAppResponse:
        app = await self._apps.get(org_id, client.client_id)
        owner = await UserDB().get_by_user_id(app.owner_user_id) if app and app.owner_user_id else None
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

    async def register(self, org_id: str, body: RegisterAppRequest, *, by_user_id: str) -> OrgAppResponse:
        keys = AppKeys.of(body.jwks)
        await self._owner_in_org(org_id, body.owner_user_id)
        thumbprint = await self._key_is_free(keys)
        client = RegisteredClient(
            kind=APP,
            org_id=org_id,
            name=body.name.strip(),
            base_url='',
            redirect_uris=[],
            jwks={'keys': [key.as_dict() for key in keys]},
            thumbprint=thumbprint,
            # The org admin registering it is the decision: an app acts
            # only in their organisation.
            state=APPROVED,
            decided_at=datetime.now(timezone.utc),
            decided_by_user_id=by_user_id,
        )
        stored = await self._clients.add(client)
        if stored.client_id != client.client_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail='Another client already registered this key'
            )
        await self._apps.add(
            org_id, OrgApp(app_id=client.client_id, owner_user_id=body.owner_user_id, created_by_user_id=by_user_id)
        )
        return await self.response(org_id, client)

    async def list(self, org_id: str) -> OrgAppList:
        return OrgAppList(apps=[await self.response(org_id, client) for client in await self._clients.apps_of(org_id)])

    async def get(self, org_id: str, app_id: str) -> OrgAppResponse:
        return await self.response(org_id, await self.client(org_id, app_id))

    async def update(self, org_id: str, app_id: str, body: UpdateAppRequest) -> OrgAppResponse:
        await self.client(org_id, app_id)
        if body.owner_user_id is not None:
            await self._owner_in_org(org_id, body.owner_user_id)
            await self._apps.set_owner(org_id, app_id, body.owner_user_id)
        if body.name is not None:
            await self._clients.rename(app_id, body.name.strip())
        return await self.get(org_id, app_id)

    async def replace_keys(self, org_id: str, app_id: str, body: ReplaceAppKeysRequest) -> OrgAppResponse:
        await self.client(org_id, app_id)
        keys = AppKeys.of(body.jwks)
        thumbprint = await self._key_is_free(keys, but=app_id)
        await self._clients.replace_keys(app_id, jwks={'keys': [key.as_dict() for key in keys]}, thumbprint=thumbprint)
        return await self.get(org_id, app_id)

    async def remove(self, org_id: str, app_id: str) -> None:
        """Forget the app: its token requests stop at once, and its tokens expire within minutes."""
        await self.client(org_id, app_id)
        await VmCredentials().revoke_app(org_id, app_id)
        await Spaces().forget_app(org_id, app_id)
        await self._apps.remove(org_id, app_id)
        await self._clients.remove(app_id)

    async def extensions(self, org_id: str, app_id: str) -> GroupExtensionList:
        """Every approved extension, each with whether this app has it on."""
        await self.client(org_id, app_id)
        on = await self._extensions.enabled_in(org_id, app_id)
        return GroupExtensionList(
            extensions=[
                GroupExtension(
                    extension_id=extension.extension_id,
                    name=str(extension.manifest.get('name') or extension.extension_id),
                    description=str(extension.manifest.get('description') or ''),
                    service_url=client.base_url,
                    enabled=extension.extension_id in on,
                )
                for extension, client in await self._shipped.distributed()
            ]
        )

    async def set_extension(
        self, org_id: str, app_id: str, extension_id: str, enabled: bool, *, by_user_id: str
    ) -> None:
        await self.client(org_id, app_id)
        if enabled:
            if await self._shipped.archive_of(extension_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail='No approved service ships this extension'
                )
            await self._extensions.enable(org_id, app_id, extension_id, by_user_id)
        else:
            await self._extensions.disable(org_id, app_id, extension_id)
