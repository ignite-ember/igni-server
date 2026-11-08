"""An extension's assets: declared in its manifest, uploaded beside its bundle.

The files an extension runs that are not Python — a JVM, a database, a
model — so a machine with no network installs it. Each is declared in
`manifest.json` with the platform it is for, a plain file name, its size,
its SHA-256 and the `source` the bundle builder fetched it from; the
manifest is inside the signed bundle, so the bundle's signature covers
every asset through its digest. See `docs/service-identity.md` §8.2.

The service uploads each asset igni does not hold, before the bundle:
`PUT /v1/clients/{client_id}/extension/assets/{sha256}`, the raw bytes,
signed in `X-Igni-Asset-Signature` — a compact JWS by the client's
registered key over `{"asset": sha256, "size": n}`. A bundle that
declares an asset igni does not hold, at the size declared, is refused.

The same rules as `igni_service.assets` and igni's manifest, so a bundle
the builder makes is one igni accepts and a machine installs.

Both ways the bytes move in chunks of `CHUNK_BYTES`, into and out of a
Postgres large object: an upload is hashed and counted as it arrives and
refused the moment it passes the size its signature claims, and a
download is read a chunk at a time, from any offset a `Range` names.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
import hashlib
import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, ValidationError

from app.db.sql.extension_assets import ExtensionAssetDB
from app.db.sql.models.extension_asset import ExtensionAsset
from app.db.sql.models.registered_client import APPROVED, RegisteredClient, SERVICE
from app.db.sql.registered_clients import RegisteredClientDB
from app.security import jose
from app.services.identity.jwk import JwkSet

#: The largest single asset a service may upload.
MAX_ASSET_BYTES = 1024 * 1024 * 1024

#: How much moves at a time, into the database and out to a machine.
CHUNK_BYTES = 1024 * 1024

#: The upload route, for the body-size middleware's larger limit.
ASSET_UPLOAD_PATH = r'/v1/clients/[^/]+/extension/assets/[0-9a-f]{64}$'

UNPACKABLE = ('.tar.gz', '.tgz', '.zip')

#: One `Range` of bytes: `bytes=first-last`, either end left open.
_RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')


class AssetRefused(ValueError):
    """The asset, or the manifest's declaration of it, cannot be accepted."""


class AssetPlatform(StrEnum):
    ANY = 'any'
    MACOS_ARM64 = 'macos-arm64'
    LINUX_X86_64 = 'linux-x86_64'
    WINDOWS_X86_64 = 'windows-x86_64'

    @classmethod
    def specific(cls) -> tuple[AssetPlatform, ...]:
        return (cls.MACOS_ARM64, cls.LINUX_X86_64, cls.WINDOWS_X86_64)


class BundleAsset(BaseModel):
    """One asset as the manifest declares it."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    name: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,63}$')
    platform: AssetPlatform
    file: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$')
    size: int = Field(gt=0, le=MAX_ASSET_BYTES)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source: str = Field(pattern=r'^https://[^\s]+$', max_length=2000)
    unpack: bool = False

    @model_validator(mode='after')
    def _unpackable(self) -> Self:
        if self.unpack and not self.file.endswith(UNPACKABLE):
            raise ValueError(f'{self.file}: only {", ".join(UNPACKABLE)} can be unpacked')
        return self


class BundleAssets(BaseModel):
    """Every asset a manifest declares, as a set: a name is for `any`, or for each platform."""

    model_config = ConfigDict(frozen=True)

    assets: list[BundleAsset] = Field(default_factory=list)

    @field_validator('assets')
    @classmethod
    def _each_name_covers_every_platform(cls, assets: list[BundleAsset]) -> list[BundleAsset]:
        by_name: dict[str, list[AssetPlatform]] = {}
        for asset in assets:
            by_name.setdefault(asset.name, []).append(asset.platform)
        for name, platforms in by_name.items():
            if platforms == [AssetPlatform.ANY]:
                continue
            if sorted(platforms) != sorted(AssetPlatform.specific()):
                raise ValueError(
                    f"asset {name!r} must be one file for 'any' platform, or one for each of "
                    f'{", ".join(AssetPlatform.specific())}; it is for {", ".join(platforms)}'
                )
        return assets

    @classmethod
    def of(cls, manifest: dict[str, object]) -> BundleAssets:
        try:
            return cls.model_validate({'assets': manifest.get('assets') or []})
        except ValidationError as exc:
            raise AssetRefused(f'manifest.json declares its assets wrongly: {exc}') from exc

    def digests(self) -> set[str]:
        return {asset.sha256 for asset in self.assets}

    def declares(self, sha256: str) -> bool:
        return sha256 in self.digests()


class AssetClaim(BaseModel):
    """What the signature over one uploaded asset says."""

    asset: str
    size: int


class AssetSignature:
    @staticmethod
    def verify(signature: str, client: RegisteredClient) -> AssetClaim:
        """The claim, if one of the client's registered keys signed it."""
        try:
            payload = JwkSet.model_validate(client.jwks).verified_payload(signature)
        except jose.TokenError as exc:
            raise AssetRefused('the signature is not a compact JWS') from exc
        if payload is not None:
            try:
                return AssetClaim.model_validate_json(payload)
            except ValidationError as exc:
                raise AssetRefused(f'the signed claim is malformed: {exc}') from exc
        raise AssetRefused("the asset is not signed by this client's registered key")


class VerifiedUpload:
    """An upload's body, in `CHUNK_BYTES` pieces, checked as it passes.

    Refuses as soon as more arrives than the signature claims, and at the
    end unless exactly that many arrived and they hash to the digest
    named. Raised from inside the store's transaction, so a refused
    upload leaves nothing behind.
    """

    def __init__(self, body: AsyncIterator[bytes], sha256: str, size: int) -> None:
        self._body = body
        self._sha256 = sha256
        self._size = size

    async def chunks(self) -> AsyncIterator[bytes]:
        digest = hashlib.sha256()
        received = 0
        pending = bytearray()
        async for piece in self._body:
            received += len(piece)
            if received > self._size:
                raise AssetRefused(f'the signature says {self._size} bytes and more than that were uploaded')
            digest.update(piece)
            pending += piece
            while len(pending) >= CHUNK_BYTES:
                yield bytes(pending[:CHUNK_BYTES])
                del pending[:CHUNK_BYTES]
        if received != self._size:
            raise AssetRefused(f'the signature says {self._size} bytes and {received} were uploaded')
        if digest.hexdigest() != self._sha256:
            raise AssetRefused('the bytes uploaded are not the asset named')
        if pending:
            yield bytes(pending)


class ByteRange(BaseModel):
    """The bytes of an asset a download sends: all of it, or what one `Range` asks for."""

    model_config = ConfigDict(frozen=True)

    start: int
    #: Inclusive, as in `Content-Range`.
    end: int
    partial: bool

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @classmethod
    def of(cls, header: str | None, size: int) -> ByteRange | None:
        """The range to send; None when the header asks for nothing the asset has (416).

        Only a single range: a multi-range request is answered with the
        whole asset, which RFC 9110 allows and no downloader of ours needs.
        """
        whole = cls(start=0, end=size - 1, partial=False)
        if not header:
            return whole
        found = _RANGE.match(header.strip())
        if found is None:
            return whole
        first, last = found.groups()
        if not first and not last:
            return whole
        if not first:
            start, end = max(size - int(last), 0), size - 1
        else:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        if start >= size or start > end:
            return None
        return cls(start=start, end=end, partial=(start, end) != (0, size - 1))


class ExtensionAssets:
    """Taking a service's assets, and checking a bundle's declarations against them."""

    def __init__(self, store: ExtensionAssetDB | None = None, clients: RegisteredClientDB | None = None) -> None:
        self._store = store or ExtensionAssetDB()
        self._clients = clients or RegisteredClientDB()

    async def holds(self, client_id: str, sha256: str) -> bool:
        return sha256 in await self._store.sizes(client_id)

    async def upload(self, client_id: str, sha256: str, body: AsyncIterator[bytes], signature: str) -> ExtensionAsset:
        """Take the asset streamed in `body`, checked against its signature before a byte is read."""
        client = await self._clients.get(client_id)
        if client is None or client.kind != SERVICE or client.state not in (APPROVED, 'pending'):
            raise AssetRefused('no such client, or it has been revoked')
        claim = AssetSignature.verify(signature, client)
        if claim.asset != sha256:
            raise AssetRefused('the signature is for a different asset than the one named')
        if not 0 < claim.size <= MAX_ASSET_BYTES:
            raise AssetRefused(f'an asset is 1 to {MAX_ASSET_BYTES} bytes')
        upload = VerifiedUpload(body, sha256, claim.size)
        return await self._store.store(
            ExtensionAsset(client_id=client_id, sha256=sha256, size=claim.size, signature=signature),
            upload.chunks(),
        )

    async def check_declared(self, client_id: str, assets: BundleAssets) -> None:
        """Every asset the bundle declares is held, at the size it declares."""
        held = await self._store.sizes(client_id)
        missing = [f'{a.name} ({a.platform})' for a in assets.assets if a.sha256 not in held]
        if missing:
            raise AssetRefused(f'upload these assets before the bundle that declares them: {", ".join(missing)}')
        wrong = [f'{a.name} ({a.platform})' for a in assets.assets if held[a.sha256] != a.size]
        if wrong:
            raise AssetRefused(f'the manifest declares a size igni does not hold for: {", ".join(wrong)}')

    async def keep_only(self, client_id: str, digests: set[str]) -> None:
        """Forget what none of the client's versions declares any more."""
        await self._store.forget_all_but(client_id, digests)

    async def held(self, client_id: str, sha256: str) -> ExtensionAsset | None:
        """The asset's record — its size and where its bytes are — without the bytes."""
        return await self._store.get(client_id, sha256)

    async def stream(self, asset: ExtensionAsset, span: ByteRange) -> AsyncIterator[bytes]:
        """The bytes `span` covers, a chunk at a time."""
        offset = span.start
        while offset <= span.end:
            length = min(CHUNK_BYTES, span.end - offset + 1)
            chunk = await self._store.read(asset.content_oid, offset, length)
            if not chunk:
                raise AssetRefused(f'the asset ended at {offset} bytes, before the {asset.size} it holds')
            yield chunk
            offset += len(chunk)


def extension_assets() -> ExtensionAssets:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return ExtensionAssets()
