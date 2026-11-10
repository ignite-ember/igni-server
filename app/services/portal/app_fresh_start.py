"""A fresh app in a discarded one's place: same name, key, owner, VM profile, extensions and credential."""

from __future__ import annotations

from pydantic import BaseModel

from app.api.base_v1.schema.portal.apps import OrgAppResponse, RegisterAppRequest
from app.db.sql.apps import AppExtensionsDB, AppsDB
from app.db.sql.models.registered_client import APP, RegisteredClient
from app.db.sql.vm_credentials import VmCredentialsDB
from app.db.sql.vm_profiles import VmProfilesDB
from app.services.cloud.vm_credentials import VmCredentials
from app.services.identity.jwk import JwkSet, SubmittedJwks
from app.services.portal.apps import OrgApps


class CarriedOver(BaseModel):
    """What a fresh app takes over from the app it replaces, read before that app is removed."""

    name: str
    jwks: SubmittedJwks
    owner_user_id: str | None
    vm_profile_id: str | None
    extension_ids: list[str]
    cloud: bool


class AppFreshStart:
    """Reads what an app carries over, and registers a new app with it once the old one is gone."""

    def __init__(
        self,
        *,
        apps: OrgApps | None = None,
        app_rows: AppsDB | None = None,
        profiles: VmProfilesDB | None = None,
        extensions: AppExtensionsDB | None = None,
        credential_rows: VmCredentialsDB | None = None,
        credentials: VmCredentials | None = None,
    ) -> None:
        self._apps = apps or OrgApps()
        self._app_rows = app_rows or AppsDB()
        self._profiles = profiles or VmProfilesDB()
        self._extensions = extensions or AppExtensionsDB()
        self._credential_rows = credential_rows or VmCredentialsDB()
        self._credentials = credentials or VmCredentials()

    async def carried_over(self, org_id: str, client: RegisteredClient) -> CarriedOver:
        app_id = client.client_id
        app = await self._app_rows.get(org_id, app_id)
        _, profile_id = await self._profiles.of_app(org_id, app_id)
        return CarriedOver(
            name=client.name,
            jwks=SubmittedJwks.of(JwkSet.model_validate(client.jwks or {})),
            owner_user_id=app.owner_user_id if app else None,
            vm_profile_id=profile_id,
            extension_ids=sorted(await self._extensions.enabled_in(org_id, app_id)),
            cloud=bool(await self._credential_rows.active(org_id, producer_kind=APP, producer_id=app_id)),
        )

    async def start(self, org_id: str, *, carried: CarriedOver, by_user_id: str) -> OrgAppResponse:
        """The new app; owned by whoever discarded the old one when its owner has left the organisation."""
        owner = carried.owner_user_id or by_user_id
        if not await self._apps.is_member(org_id, owner):
            owner = by_user_id
        fresh = await self._apps.register(
            org_id,
            body=RegisterAppRequest(name=carried.name, owner_user_id=owner, jwks=carried.jwks),
            by_user_id=by_user_id,
        )
        if carried.vm_profile_id is not None:
            await self._profiles.assign_app(org_id, fresh.app_id, carried.vm_profile_id)
        for extension_id in carried.extension_ids:
            await self._extensions.enable(org_id, fresh.app_id, extension_id, by_user_id)
        if carried.cloud:
            await self._credentials.enable_app(org_id, fresh.app_id, by_user_id=by_user_id)
        return await self._apps.get(org_id, fresh.app_id)
