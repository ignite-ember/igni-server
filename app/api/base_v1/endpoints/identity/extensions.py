"""Extensions: a service uploading its own, and machines receiving them.

`PUT /v1/clients/{client_id}/extension` takes the bundle as the raw body
(`application/gzip`) and its signature in `X-Igni-Bundle-Signature`. No
other authentication: the signature, by the key the client registered,
is the proof that the client sent it. An approved service's version that
asks for more than the approved one is accepted and **held** — `held:
true`, with the reason and what it adds — and machines keep receiving
the approved version until a super admin approves it (§8.1).

`PUT /v1/clients/{client_id}/extension/assets/{sha256}` takes one of the
assets the bundle declares — a JVM, a database, a model — before the
bundle, signed in `X-Igni-Asset-Signature`; `HEAD` on the same path says
whether igni holds it already (§8.2).

`GET /v1/extensions` and `GET /v1/extensions/{id}/bundle` are what a
signed-in machine installs from — every approved service's extension
that an org admin has switched on for the person's group (§8.3), or,
with an app's token, for that app (§11) — and
`GET /v1/extensions/{id}/assets/{sha256}` the assets for its own
platform. A person is in exactly one group; an extension that is not on
for it is not listed, and its bundle and assets are a 404 to them. The machine checks the signature again
against the publisher's key before installing, and each asset against
the digest the signed manifest pins, so a bundle altered anywhere on the
way does not run.

Assets stream both ways — an upload goes into the database as it
arrives, a download leaves it a chunk at a time — and a download answers
a single `Range`, so a machine on a poor link resumes a several-hundred-
megabyte file instead of starting it again.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.base_v1.schema.extensions import Distributed, HeldAsset, Published
from app.security.producer import current_producer, ProducerCaller
from app.services.identity.extension_assets import AssetRefused, extension_assets, ExtensionAssets
from app.services.identity.extension_distribution import (
    AssetNotDistributed,
    extension_distribution,
    ExtensionDistribution,
    ExtensionNotDistributed,
)
from app.services.identity.extensions import (
    ExtensionConflict,
    ExtensionRefused,
    MAX_ARCHIVE_BYTES,
    service_extensions,
    ServiceExtensions,
)

router = APIRouter()

SIGNATURE_HEADER = 'X-Igni-Bundle-Signature'
ASSET_SIGNATURE_HEADER = 'X-Igni-Asset-Signature'

#: An asset's name in a path: its digest. Checked in the handler, not by
#: FastAPI, so a malformed name is the same "no" as an unknown one rather
#: than a 422 that would answer before anything else is asked.
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


@router.put('/clients/{client_id}/extension', response_model=Published, tags=['Identity'])
async def publish(
    client_id: str,
    request: Request,
    signature: str = Header(..., alias=SIGNATURE_HEADER),
    extensions: ServiceExtensions = Depends(service_extensions),
) -> Published:
    archive = await request.body()
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail='The bundle is too large')
    try:
        publication = await extensions.publish(client_id, archive, signature)
    except ExtensionRefused as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ExtensionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Published.of(publication)


@router.get('/clients/{client_id}/extension', response_model=Published, tags=['Identity'])
async def published(client_id: str, extensions: ServiceExtensions = Depends(service_extensions)) -> Published:
    """What igni holds for this client, so a service starting up uploads
    only when its bundle has changed: its latest upload — the held one,
    if a version is waiting for a super admin, so a restart does not send
    it again. Public: an id, a version and a digest say nothing a signed
    bundle does not."""
    latest = await extensions.latest(client_id)
    if latest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Nothing uploaded for this client')
    return Published.of(latest)


@router.get('/extensions', response_model=Distributed, tags=['Extensions'])
async def distributed(
    caller: ProducerCaller = Depends(current_producer),
    distribution: ExtensionDistribution = Depends(extension_distribution),
) -> Distributed:
    """What this producer gets: a person, through their one group; an app, through its own list."""
    return await distribution.listing(caller)


@router.get('/extensions/{extension_id}/bundle', tags=['Extensions'])
async def bundle(
    extension_id: str,
    caller: ProducerCaller = Depends(current_producer),
    distribution: ExtensionDistribution = Depends(extension_distribution),
) -> Response:
    try:
        found = await distribution.bundle(extension_id=extension_id, caller=caller)
    except ExtensionNotDistributed as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(
        content=found.archive,
        media_type='application/gzip',
        headers={SIGNATURE_HEADER: found.signature, 'X-Igni-Bundle-Sha256': found.sha256},
    )


@router.head('/clients/{client_id}/extension/assets/{sha256}', tags=['Identity'])
async def holds_asset(client_id: str, sha256: str, assets: ExtensionAssets = Depends(extension_assets)) -> Response:
    """Whether igni holds this asset for this client. Public, like the
    bundle's digest: a digest says nothing the signed manifest does not."""
    if not _SHA256.match(sha256) or not await assets.holds(client_id, sha256):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return Response(status_code=status.HTTP_200_OK)


@router.put('/clients/{client_id}/extension/assets/{sha256}', response_model=HeldAsset, tags=['Identity'])
async def upload_asset(
    client_id: str,
    sha256: str,
    request: Request,
    signature: str = Header(..., alias=ASSET_SIGNATURE_HEADER),
    assets: ExtensionAssets = Depends(extension_assets),
) -> HeldAsset:
    if not _SHA256.match(sha256):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='An asset is named by its SHA-256')
    try:
        stored = await assets.upload(client_id, sha256, request.stream(), signature)
    except AssetRefused as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return HeldAsset(sha256=stored.sha256, size=stored.size)


@router.get('/extensions/{extension_id}/assets/{sha256}', tags=['Extensions'])
async def asset(
    extension_id: str,
    sha256: str,
    byte_range: str | None = Header(None, alias='Range'),
    caller: ProducerCaller = Depends(current_producer),
    distribution: ExtensionDistribution = Depends(extension_distribution),
) -> Response:
    """One asset of a distributed extension — only one its live manifest declares — whole or one range of it."""
    try:
        download = await distribution.asset(
            extension_id=extension_id, sha256=sha256, caller=caller, byte_range=byte_range
        )
    except AssetNotDistributed as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if not download.satisfiable:
        return Response(status_code=download.status_code, headers=download.headers())
    return StreamingResponse(
        distribution.stream(download),
        status_code=download.status_code,
        media_type='application/octet-stream',
        headers=download.headers(),
    )
