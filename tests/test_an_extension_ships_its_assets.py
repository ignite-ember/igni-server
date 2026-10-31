"""An extension's assets: uploaded beside its bundle, one per digest, signed.

A database, a JVM, a model — the files an extension runs that are not
Python — so a machine with no network installs it. The manifest inside
the signed bundle pins each one's size and digest; igni refuses a bundle
declaring one it does not hold, keeps one only while a version declares
it, and hands a machine only what the distributed manifest declares.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest
from sqlalchemy import text

from app.config import settings
from app.db.sql.registered_clients import RegisteredClientDB
from app.middlewares.body_size_limit import BodySizeLimitMiddleware
from app.services.identity.extension_assets import ByteRange, CHUNK_BYTES, MAX_ASSET_BYTES
from tests.conftest import TEST_DB_URL_ASYNC, the_app
from tests.entitled_person import EntitledPerson
from tests.registered_service import ExtensionAssetFile, ExtensionBundle, RegisteredService

pytestmark = pytest.mark.anyio

JRES = [
    ExtensionAssetFile('jre', platform, f'a jre for {platform}'.encode(), unpack=True)
    for platform in ('macos-arm64', 'linux-x86_64', 'windows-x86_64')
]
MODEL = ExtensionAssetFile('model', 'any', b'the same model everywhere')
ASSETS = [*JRES, MODEL]


def _manifest(version: str = '1.0.0', assets: list[ExtensionAssetFile] | None = None) -> dict[str, object]:
    return {
        'id': 'igni.codeindex',
        'version': version,
        'name': 'CodeIndex',
        'assets': [asset.declaration for asset in (ASSETS if assets is None else assets)],
    }


async def _put_asset(client, service: RegisteredService, asset: ExtensionAssetFile, signature: str | None = None):
    return await client.put(
        f'/clients/{service.client_id}/extension/assets/{asset.sha256}',
        content=asset.content,
        headers={
            'Content-Type': 'application/octet-stream',
            'X-Igni-Asset-Signature': signature or asset.signed_by(service),
        },
    )


async def _put_bundle(client, service: RegisteredService, manifest: dict[str, object]):
    bundle = ExtensionBundle.of(manifest)
    return await client.put(
        f'/clients/{service.client_id}/extension',
        content=bundle.archive,
        headers={
            'Content-Type': 'application/gzip',
            'X-Igni-Bundle-Signature': bundle.signed_by(
                service, extension=str(manifest['id']), version=str(manifest['version'])
            ),
        },
    )


async def _person(db) -> dict[str, str]:
    return await EntitledPerson(db).headers('igni.codeindex')


async def _shipped(client) -> RegisteredService:
    service = RegisteredService()
    await service.approve()
    for asset in ASSETS:
        assert (await _put_asset(client, service, asset)).status_code == 200
    assert (await _put_bundle(client, service, _manifest())).status_code == 200
    return service


class TestUploading:
    async def test_a_signed_asset_is_taken_and_then_known(self, client):
        service = RegisteredService()
        await service.approve()
        path = f'/clients/{service.client_id}/extension/assets/{MODEL.sha256}'
        assert (await client.head(path)).status_code == 404

        response = await _put_asset(client, service, MODEL)

        assert response.status_code == 200, response.text
        assert response.json() == {'sha256': MODEL.sha256, 'size': len(MODEL.content)}
        assert (await client.head(path)).status_code == 200

    async def test_bytes_that_are_not_the_named_digest(self, client):
        service = RegisteredService()
        await service.approve()
        response = await client.put(
            f'/clients/{service.client_id}/extension/assets/{MODEL.sha256}',
            content=b'other bytes',
            headers={'X-Igni-Asset-Signature': MODEL.signed_by(service)},
        )
        assert response.status_code == 400

    async def test_a_signature_for_another_size(self, client):
        service = RegisteredService()
        await service.approve()
        response = await _put_asset(client, service, MODEL, MODEL.signed_by(service, size=1))
        assert response.status_code == 400

    async def test_a_signature_for_another_asset(self, client):
        service = RegisteredService()
        await service.approve()
        response = await _put_asset(client, service, MODEL, MODEL.signed_by(service, sha256=JRES[0].sha256))
        assert response.status_code == 400

    async def test_signed_by_another_service(self, client):
        service, other = RegisteredService(), RegisteredService()
        await service.approve()
        await other.approve()
        assert (await _put_asset(client, service, MODEL, MODEL.signed_by(other))).status_code == 400

    async def test_a_revoked_service_may_not(self, client):
        service = RegisteredService()
        await service.approve(state='revoked')
        assert (await _put_asset(client, service, MODEL)).status_code == 400

    async def test_a_name_that_is_not_a_digest(self, client):
        service = RegisteredService()
        await service.approve()
        response = await client.put(
            f'/clients/{service.client_id}/extension/assets/not-a-digest',
            content=MODEL.content,
            headers={'X-Igni-Asset-Signature': MODEL.signed_by(service)},
        )
        assert response.status_code == 400

    def test_the_asset_route_has_its_own_larger_limit(self):
        app = the_app()
        configured = next(m for m in app.user_middleware if m.cls is BodySizeLimitMiddleware)
        limiter = BodySizeLimitMiddleware(app, **configured.kwargs)

        assert limiter._limit_for(f'/v1/clients/abc/extension/assets/{MODEL.sha256}') == MAX_ASSET_BYTES
        assert limiter._limit_for('/v1/clients/abc/extension/assets/x') == settings.max_request_body_bytes


class TestTheBundleDeclaresThem:
    async def test_a_bundle_whose_assets_are_all_held_is_accepted(self, client):
        await _shipped(client)

    async def test_a_bundle_declaring_one_not_uploaded_is_refused(self, client):
        service = RegisteredService()
        await service.approve()
        for asset in JRES:
            await _put_asset(client, service, asset)

        response = await _put_bundle(client, service, _manifest())

        assert response.status_code == 400
        assert 'model (any)' in response.json()['detail']

    async def test_a_platform_left_out_is_refused(self, client):
        service = RegisteredService()
        await service.approve()
        for asset in JRES[:2]:
            await _put_asset(client, service, asset)

        response = await _put_bundle(client, service, _manifest(assets=JRES[:2]))

        assert response.status_code == 400
        assert 'one for each of' in response.json()['detail']

    async def test_a_declared_size_igni_does_not_hold(self, client):
        service = RegisteredService()
        await service.approve()
        await _put_asset(client, service, MODEL)
        lying = ExtensionAssetFile('model', 'any', MODEL.content)
        lying.declaration['size'] = len(MODEL.content) + 1

        response = await _put_bundle(client, service, _manifest(assets=[lying]))

        assert response.status_code == 400

    async def test_another_services_asset_does_not_count(self, client):
        owner, other = RegisteredService(), RegisteredService()
        await owner.approve()
        await other.approve()
        await _put_asset(client, owner, MODEL)

        response = await _put_bundle(client, other, _manifest(assets=[MODEL]))

        assert response.status_code == 400

    async def test_an_asset_no_version_declares_any_more_is_forgotten(self, client):
        service = await _shipped(client)
        newer = ExtensionAssetFile('model', 'any', b'a newer model')
        await _put_asset(client, service, newer)

        assert (await _put_bundle(client, service, _manifest('1.1.0', [*JRES, newer]))).status_code == 200

        gone = f'/clients/{service.client_id}/extension/assets/{MODEL.sha256}'
        kept = f'/clients/{service.client_id}/extension/assets/{JRES[0].sha256}'
        assert (await client.head(gone)).status_code == 404
        assert (await client.head(kept)).status_code == 200


class TestAMachineDownloadsThem:
    async def test_a_declared_asset_of_a_distributed_extension(self, client, db):
        await _shipped(client)

        response = await client.get(f'/extensions/igni.codeindex/assets/{JRES[1].sha256}', headers=await _person(db))

        assert response.status_code == 200
        assert response.content == JRES[1].content
        assert response.headers['x-igni-asset-sha256'] == JRES[1].sha256

    async def test_not_one_the_manifest_does_not_declare(self, client, db):
        await _shipped(client)
        stray = ExtensionAssetFile('stray', 'any', b'never declared')

        response = await client.get(f'/extensions/igni.codeindex/assets/{stray.sha256}', headers=await _person(db))

        assert response.status_code == 404

    async def test_not_from_a_pending_service(self, client, db):
        service = RegisteredService()
        await service.approve(state='pending')
        for asset in ASSETS:
            await _put_asset(client, service, asset)
        await _put_bundle(client, service, _manifest())

        response = await client.get(f'/extensions/igni.codeindex/assets/{MODEL.sha256}', headers=await _person(db))

        assert response.status_code == 404

    async def test_only_for_somebody_signed_in(self, client):
        assert (await client.get(f'/extensions/igni.codeindex/assets/{MODEL.sha256}')).status_code == 401


#: An asset several chunks long, so every boundary is crossed.
LARGE = ExtensionAssetFile('model', 'any', bytes(range(256)) * (CHUNK_BYTES * 5 // 512) + b'tail')


async def _large_objects() -> int:
    """Through the app's own engine: the `db` fixture runs on another loop."""
    async with RegisteredClientDB().session_scope() as session:
        return int((await session.execute(text('SELECT count(*) FROM pg_largeobject_metadata'))).scalar_one())


async def _shipped_large(client) -> RegisteredService:
    service = RegisteredService()
    await service.approve()
    for asset in [*JRES, LARGE]:
        assert (await _put_asset(client, service, asset)).status_code == 200
    assert (await _put_bundle(client, service, _manifest(assets=[*JRES, LARGE]))).status_code == 200
    return service


class TestItIsKeptAsALargeObject:
    async def test_several_chunks_come_back_exactly(self, client, db):
        await _shipped_large(client)

        response = await client.get(f'/extensions/igni.codeindex/assets/{LARGE.sha256}', headers=await _person(db))

        assert response.status_code == 200
        assert response.content == LARGE.content
        assert response.headers['content-length'] == str(len(LARGE.content))
        assert response.headers['etag'] == f'"{LARGE.sha256}"'
        assert response.headers['accept-ranges'] == 'bytes'

    async def test_more_bytes_than_signed_are_refused_and_nothing_is_kept(self, client):
        service = RegisteredService()
        await service.approve()

        response = await client.put(
            f'/clients/{service.client_id}/extension/assets/{LARGE.sha256}',
            content=LARGE.content + b'x',
            headers={'X-Igni-Asset-Signature': LARGE.signed_by(service)},
        )

        assert response.status_code == 400
        assert (await client.head(f'/clients/{service.client_id}/extension/assets/{LARGE.sha256}')).status_code == 404
        assert await _large_objects() == 0

    async def test_uploading_a_held_asset_again_leaves_one_object(self, client):
        service = RegisteredService()
        await service.approve()

        for _ in range(2):
            assert (await _put_asset(client, service, MODEL)).status_code == 200

        assert await _large_objects() == 1

    async def test_two_uploads_of_one_asset_at_once_both_succeed_and_keep_one_object(self, client):
        service = RegisteredService()
        await service.approve()

        responses = await asyncio.gather(*(_put_asset(client, service, LARGE) for _ in range(2)))

        assert [r.status_code for r in responses] == [200, 200]
        assert await _large_objects() == 1

    async def test_an_asset_forgotten_takes_its_object_with_it(self, client):
        service = await _shipped(client)
        newer = ExtensionAssetFile('model', 'any', b'a newer model')
        await _put_asset(client, service, newer)
        assert await _large_objects() == len(ASSETS) + 1

        assert (await _put_bundle(client, service, _manifest('1.1.0', [*JRES, newer]))).status_code == 200

        assert await _large_objects() == len(ASSETS)

    async def test_a_removed_service_leaves_no_object_behind(self, client):
        service = await _shipped(client)
        assert await _large_objects() == len(ASSETS)

        assert await RegisteredClientDB().remove(service.client_id)

        assert await _large_objects() == 0


class TestADownloadResumes:
    async def _get(self, client, db, byte_range: str):
        return await client.get(
            f'/extensions/igni.codeindex/assets/{LARGE.sha256}', headers={**await _person(db), 'Range': byte_range}
        )

    async def test_from_an_offset_to_the_end(self, client, db):
        await _shipped_large(client)
        start = CHUNK_BYTES + 17

        response = await self._get(client, db, f'bytes={start}-')

        assert response.status_code == 206
        assert response.content == LARGE.content[start:]
        assert response.headers['content-range'] == f'bytes {start}-{len(LARGE.content) - 1}/{len(LARGE.content)}'

    async def test_a_span_inside_it(self, client, db):
        await _shipped_large(client)

        response = await self._get(client, db, f'bytes=10-{CHUNK_BYTES * 2}')

        assert response.status_code == 206
        assert response.content == LARGE.content[10 : CHUNK_BYTES * 2 + 1]

    async def test_the_last_bytes(self, client, db):
        await _shipped_large(client)

        response = await self._get(client, db, 'bytes=-4')

        assert response.status_code == 206
        assert response.content == b'tail'

    async def test_past_the_end_is_416(self, client, db):
        await _shipped_large(client)

        response = await self._get(client, db, f'bytes={len(LARGE.content)}-')

        assert response.status_code == 416
        assert response.headers['content-range'] == f'bytes */{len(LARGE.content)}'


class TestTheRangeHeader:
    @pytest.mark.parametrize(
        ('header', 'expected'),
        [
            (None, (0, 99, False)),
            ('bytes=0-', (0, 99, False)),
            ('bytes=0-99', (0, 99, False)),
            ('bytes=10-19', (10, 19, True)),
            ('bytes=90-500', (90, 99, True)),
            ('bytes=-10', (90, 99, True)),
            ('bytes=-500', (0, 99, False)),
            ('bytes=0-1,5-6', (0, 99, False)),
            ('items=0-5', (0, 99, False)),
        ],
    )
    def test_what_it_sends(self, header, expected):
        span = ByteRange.of(header, 100)
        assert span is not None
        assert (span.start, span.end, span.partial) == expected

    @pytest.mark.parametrize('header', ['bytes=100-', 'bytes=50-10'])
    def test_what_it_cannot(self, header):
        assert ByteRange.of(header, 100) is None


#: Uploads and downloads a 200 MB asset in a fresh interpreter, so its
#: peak RSS is this file's alone, and prints how far the peak grew.
_MEMORY_PROBE = """
import asyncio, hashlib, resource, sys
import app.main
from app.config import Settings
Settings.postgres_url = property(lambda self: sys.argv[1])
from app.db.sql import SQLAlchemySessionManager
from app.services.identity.extension_assets import ByteRange, ExtensionAssets
from tests.registered_service import ExtensionAssetFile, RegisteredService

PIECE = 64 * 1024
SIZE = 200 * 1024 * 1024
def piece(i):
    return i.to_bytes(8, 'big') * (PIECE // 8)

def peak():
    used = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return used if sys.platform == 'darwin' else used * 1024

async def main():
    digest = hashlib.sha256()
    for i in range(SIZE // PIECE):
        digest.update(piece(i))
    service = RegisteredService()
    await service.approve()
    asset = ExtensionAssetFile('model', 'any', b'')
    asset.content, asset.sha256 = b'', digest.hexdigest()
    signature = asset.signed_by(service, size=SIZE, sha256=asset.sha256)

    async def body():
        for i in range(SIZE // PIECE):
            yield piece(i)

    assets = ExtensionAssets()
    before = peak()
    stored = await assets.upload(service.client_id, asset.sha256, body(), signature)
    read = hashlib.sha256()
    async for chunk in assets.stream(stored, ByteRange.of(None, SIZE)):
        read.update(chunk)
    grown = peak() - before
    await SQLAlchemySessionManager.dispose_all()
    assert read.hexdigest() == asset.sha256
    print(grown)

asyncio.run(main())
"""


class TestItNeverHoldsTheFile:
    def test_a_200_mb_asset_up_and_down_grows_the_peak_by_a_few_chunks(self):
        done = subprocess.run(
            [sys.executable, '-c', _MEMORY_PROBE, TEST_DB_URL_ASYNC],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=os.getcwd(),
            env={**os.environ, 'PYTHONPATH': os.getcwd()},
        )

        assert done.returncode == 0, done.stderr
        grown = int(done.stdout.strip().splitlines()[-1])
        sys.stderr.write(f'peak RSS grew by {grown / 1024 / 1024:.1f} MB for a 200 MB asset\n')
        assert grown < 64 * 1024 * 1024
