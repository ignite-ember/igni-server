"""What a signed-in machine installs from: the extensions its producer gets, their bundles and assets.

A person gets every approved service's extension that an org admin has
switched on for their one group (§8.3); an app, or one of its spaces,
the extensions on the app's own list (§11). An extension the producer
does not get is not listed, and its bundle and assets are refused
exactly as an unknown one's are, so a machine cannot tell the two apart.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.api.base_v1.schema.extensions import Distributed, DistributedExtension, ListedProducer
from app.db.sql.models.registered_client import RegisteredClient
from app.db.sql.models.service_extension import ServiceExtension
from app.db.sql.service_extensions import ServiceExtensionDB
from app.security.producer import ProducerCaller
from app.services.identity.asset_download import AssetDownload
from app.services.identity.extension_assets import BundleAssets, ExtensionAssets
from app.services.identity.extension_manifest import Manifest
from app.services.identity.jwk import JwkSet
from app.services.identity.priority import ExtensionPriority
from app.services.portal.group_extensions import Entitlement, GroupExtensions


class ExtensionNotDistributed(LookupError):
    """No approved service ships this extension to this producer."""


class AssetNotDistributed(LookupError):
    """No extension distributed to this producer declares this asset, or igni does not hold it."""


class ExtensionDistribution:
    def __init__(
        self,
        *,
        entitlements: GroupExtensions | None = None,
        store: ServiceExtensionDB | None = None,
        priority: ExtensionPriority | None = None,
        assets: ExtensionAssets | None = None,
    ) -> None:
        self._entitlements = entitlements or GroupExtensions()
        self._store = store or ServiceExtensionDB()
        self._priority = priority or ExtensionPriority(extensions=self._store)
        self._assets = assets or ExtensionAssets()

    async def listing(self, caller: ProducerCaller) -> Distributed:
        """What this producer gets: a person, through their one group; an app, through its own list."""
        entitlement = await self._entitlements.of_producer(caller)
        return Distributed(
            producer=ListedProducer(kind=caller.kind, id=caller.id),
            group=entitlement.group,
            group_name=entitlement.group_name,
            extensions=[
                self._listed(extension, client, entitlement)
                for extension, client in await self._store.distributed()
                if entitlement.allows(extension.extension_id)
            ],
            priority=[
                extension_id for extension_id in await self._priority.distributed() if entitlement.allows(extension_id)
            ],
        )

    async def bundle(self, *, extension_id: str, caller: ProducerCaller) -> ServiceExtension:
        """The extension's live version with its archive, when this producer gets it."""
        entitlement = await self._entitlements.of_producer(caller)
        found = await self._store.archive_of(extension_id) if entitlement.allows(extension_id) else None
        if found is None:
            raise ExtensionNotDistributed('No approved service ships this extension')
        return found

    async def asset(
        self, *, extension_id: str, sha256: str, caller: ProducerCaller, byte_range: str | None
    ) -> AssetDownload:
        """One asset of an extension this producer gets — only one its live manifest declares."""
        try:
            found = await self.bundle(extension_id=extension_id, caller=caller)
        except ExtensionNotDistributed as exc:
            raise AssetNotDistributed('No distributed extension declares this asset') from exc
        if not BundleAssets.of(found.manifest).declares(sha256):
            raise AssetNotDistributed('No distributed extension declares this asset')
        held = await self._assets.held(found.client_id, sha256)
        if held is None:
            raise AssetNotDistributed('igni does not hold this asset')
        return AssetDownload.of(held, byte_range)

    def stream(self, download: AssetDownload) -> AsyncIterator[bytes]:
        """The bytes the download sends, a chunk at a time."""
        return self._assets.stream(download.asset, download.sending)

    @staticmethod
    def _listed(
        extension: ServiceExtension, client: RegisteredClient, entitlement: Entitlement
    ) -> DistributedExtension:
        manifest = Manifest.model_validate(extension.manifest)
        return DistributedExtension(
            extension_id=extension.extension_id,
            version=extension.version,
            name=manifest.name or extension.extension_id,
            description=manifest.description,
            service_url=client.base_url,
            sha256=extension.sha256,
            signature=extension.signature,
            publisher_jwks=JwkSet.model_validate(client.jwks),
            published_at=extension.published_at,
            groups=entitlement.enabled[extension.extension_id],
            apps=entitlement.apps.get(extension.extension_id, []),
        )


def extension_distribution() -> ExtensionDistribution:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return ExtensionDistribution()
