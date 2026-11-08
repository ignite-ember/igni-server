"""A service's extension: checked on upload, published live or held.

The bundle and its signature are checked in
`app.services.identity.extension_bundle`.

Uploading is allowed while the client is still pending, because a super
admin deciding whether to approve a service needs to see what it would
put on people's machines. Distribution waits for approval.

Approval covers what the super admin saw. Once the service is approved,
a new version that asks for the same or less
(`app.services.identity.grants_comparison`) goes live at once; one that
asks for **more** is held: machines keep receiving the approved version,
and a super admin approves or rejects the held one on the Services page
(`app.services.identity.held_updates`). A newer upload replaces a held
one. The upload still succeeds, and says it is held and why, so the
service can log it.

What the extension changes in igni is read off the manifest too
(`app.services.identity.impact`), for the Services page. A collision
never holds an upload back: it is settled by the super admin's priority
order, here and on every machine.
"""

from __future__ import annotations

from app.db.sql.models.registered_client import APPROVED, PENDING, SERVICE
from app.db.sql.models.service_extension import HELD, LIVE, ServiceExtension
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.service_extensions import ServiceExtensionDB
from app.services.identity.extension_assets import AssetRefused, BundleAssets, ExtensionAssets
from app.services.identity.extension_bundle import BundleClaim, ExtensionBundle
from app.services.identity.extension_errors import ExtensionConflict, ExtensionRefused
from app.services.identity.extension_grants import ExtensionGrants
from app.services.identity.extension_publication import Publication
from app.services.identity.grants_comparison import GrantsComparison
from app.services.identity.impact import ExtensionImpact, IgniCommands, ImpactRefused


class ServiceExtensions:
    def __init__(
        self,
        store: ServiceExtensionDB | None = None,
        clients: RegisteredClientDB | None = None,
        assets: ExtensionAssets | None = None,
    ) -> None:
        self._store = store or ServiceExtensionDB()
        self._clients = clients or RegisteredClientDB()
        self._assets = assets or ExtensionAssets(clients=self._clients)

    async def publish(self, client_id: str, archive: bytes, signature: str) -> Publication:
        client = await self._clients.get(client_id)
        if client is None or client.kind != SERVICE or client.state not in (APPROVED, PENDING):
            raise ExtensionRefused('no such client, or it has been revoked')
        claim = BundleClaim.signed(signature, client)
        bundle = ExtensionBundle(archive)
        if claim.sha256 != bundle.sha256:
            raise ExtensionRefused('the signature is for different bytes than were uploaded')
        manifest = bundle.manifest()
        if (manifest.id, manifest.version) != (claim.extension, claim.version):
            raise ExtensionRefused('the signature names a different extension or version than the manifest')
        owner = await self._store.approved_owner_of(manifest.id)
        if owner is not None and owner != client_id:
            raise ExtensionConflict(f'another approved service already ships {manifest.id}')
        raw = manifest.model_dump(mode='json')
        try:
            await self._assets.check_declared(client_id, BundleAssets.of(raw))
        except AssetRefused as exc:
            raise ExtensionRefused(str(exc)) from exc
        try:
            impact = ExtensionImpact.of(raw)
        except ImpactRefused as exc:
            raise ExtensionRefused(str(exc)) from exc
        igni_own = IgniCommands.igni().clashes(impact)
        if igni_own:
            raise ExtensionRefused(
                f"{', '.join(igni_own)} {'is' if len(igni_own) == 1 else 'are'} igni's own: "
                'igni answers its own commands before any extension, so these would never run'
            )
        upload = ServiceExtension(
            client_id=client_id,
            stage=LIVE,
            extension_id=manifest.id,
            version=manifest.version,
            manifest=raw,
            plugin=bundle.plugin(manifest).model_dump(mode='json'),
            impact=impact.model_dump(mode='json'),
            archive=archive,
            sha256=bundle.sha256,
            signature=signature,
        )
        # Approval covers the version the super admin saw. A pending
        # service's upload is what they will see; a first upload has
        # nothing to be compared with, as before.
        live = await self._store.of(client_id) if client.state == APPROVED else None
        comparison = (
            GrantsComparison.between(ExtensionGrants.of_version(live), ExtensionGrants.of_version(upload))
            if live is not None
            else None
        )
        if comparison is not None and comparison.wider:
            held = await self._store.held_of(client_id)
            if held is not None and held.sha256 == upload.sha256:
                # The same bytes again — a restart. It keeps its place.
                return Publication(extension=held, held=True, comparison=comparison)
            upload.stage = HELD
        stored = await self._store.put(upload)
        await self.keep_only_declared(client_id)
        return Publication(extension=stored, held=stored.stage == HELD, comparison=comparison)

    async def latest(self, client_id: str) -> Publication | None:
        """The client's latest upload: the held one, if a version is waiting for a super admin, else the live one."""
        held = await self._store.held_of(client_id)
        found = held or await self._store.of(client_id)
        return Publication(extension=found, held=held is not None) if found is not None else None

    async def keep_only_declared(self, client_id: str) -> None:
        """An asset neither the live nor the held version declares is not distributed again; the bytes go."""
        digests: set[str] = set()
        for row in (await self._store.of(client_id), await self._store.held_of(client_id)):
            if row is not None:
                digests |= BundleAssets.of(row.manifest).digests()
        await self._assets.keep_only(client_id, digests)


def service_extensions() -> ServiceExtensions:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return ServiceExtensions()
