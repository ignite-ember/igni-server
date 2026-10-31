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

from datetime import datetime
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.db.sql.service_extensions import ServiceExtensionDB
from app.security.producer import current_producer, ProducerCaller
from app.services.identity.extension_assets import AssetRefused, BundleAssets, ByteRange, ExtensionAssets
from app.services.identity.extension_grants import AddedGrant
from app.services.identity.extensions import ExtensionConflict, ExtensionRefused, MAX_ARCHIVE_BYTES, ServiceExtensions
from app.services.identity.priority import ExtensionPriority
from app.services.portal.group_extensions import GroupExtensions

router = APIRouter()

SIGNATURE_HEADER = 'X-Igni-Bundle-Signature'
ASSET_SIGNATURE_HEADER = 'X-Igni-Asset-Signature'

#: An asset's name in a path: its digest. Checked in the handler, not by
#: FastAPI, so a malformed name is the same "no" as an unknown one rather
#: than a 422 that would answer before anything else is asked.
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


class HeldAsset(BaseModel):
    sha256: str
    size: int


class Published(BaseModel):
    extension_id: str
    version: str
    sha256: str
    #: Held for a super admin: it asks for more than the approved version,
    #: which machines keep receiving until it is approved.
    held: bool = False
    #: Why it is held, in a line a service can log.
    reason: str | None = None
    #: What it asks for that the approved version did not.
    added: list[AddedGrant] = []


class DistributedExtension(BaseModel):
    extension_id: str
    version: str
    name: str
    description: str
    #: The service it belongs to — and the audience of the tokens the
    #: host fetches for it.
    service_url: str
    sha256: str
    #: Verify with `publisher_jwks` before installing.
    signature: str
    publisher_jwks: dict[str, object]
    published_at: datetime
    #: Every group in the producer's organisation this extension is on
    #: for. A person gets it because theirs is one of them.
    groups: list[str]
    #: Every app in the organisation this extension is on for. An app
    #: gets it because it is one of them.
    apps: list[str]


class ListedProducer(BaseModel):
    """Who the listing is for: a person, or an app (`docs/service-identity.md` §11)."""

    kind: str
    id: str


class Distributed(BaseModel):
    producer: ListedProducer
    #: The person's one group in the organisation they are signed in to,
    #: read now — fresher than the session token's `group` claim. Null
    #: for an app, which is in no group, and for somebody in no
    #: organisation, who gets nothing.
    group: str | None
    extensions: list[DistributedExtension]
    #: The super admin's priority order of extensions, highest first. A
    #: machine gives every collision to the higher one, stacks prompt
    #: edits and asks hooks in it.
    priority: list[str]


@router.put('/clients/{client_id}/extension', response_model=Published, tags=['Identity'])
async def publish(
    client_id: str,
    request: Request,
    signature: str = Header(..., alias=SIGNATURE_HEADER),
) -> Published:
    archive = await request.body()
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail='The bundle is too large')
    try:
        publication = await ServiceExtensions().publish(client_id, archive, signature)
    except ExtensionRefused as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ExtensionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    stored = publication.extension
    comparison = publication.comparison
    return Published(
        extension_id=stored.extension_id,
        version=stored.version,
        sha256=stored.sha256,
        held=publication.held,
        reason=f'held for a super admin: {comparison.reason}' if publication.held and comparison else None,
        added=list(comparison.added) if publication.held and comparison else [],
    )


@router.get('/clients/{client_id}/extension', response_model=Published, tags=['Identity'])
async def published(client_id: str) -> Published:
    """What igni holds for this client, so a service starting up uploads
    only when its bundle has changed: its latest upload — the held one,
    if a version is waiting for a super admin, so a restart does not send
    it again. Public: an id, a version and a digest say nothing a signed
    bundle does not."""
    store = ServiceExtensionDB()
    held = await store.held_of(client_id)
    found = held or await store.of(client_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Nothing uploaded for this client')
    return Published(
        extension_id=found.extension_id,
        version=found.version,
        sha256=found.sha256,
        held=held is not None,
        reason='held for a super admin: it asks for more than the approved version' if held is not None else None,
    )


@router.get('/extensions', response_model=Distributed, tags=['Extensions'])
async def distributed(caller: ProducerCaller = Depends(current_producer)) -> Distributed:
    """What this producer gets: a person, through their one group; an app, through its own list."""
    entitlement = await GroupExtensions().of_producer(caller)
    return Distributed(
        producer=ListedProducer(kind=caller.kind, id=caller.id),
        group=entitlement.group,
        extensions=[
            DistributedExtension(
                extension_id=extension.extension_id,
                version=extension.version,
                name=str(extension.manifest.get('name') or extension.extension_id),
                description=str(extension.manifest.get('description') or ''),
                service_url=client.base_url,
                sha256=extension.sha256,
                signature=extension.signature,
                publisher_jwks=dict(client.jwks),
                published_at=extension.published_at,
                groups=entitlement.enabled[extension.extension_id],
                apps=entitlement.apps.get(extension.extension_id, []),
            )
            for extension, client in await ServiceExtensionDB().distributed()
            if entitlement.allows(extension.extension_id)
        ],
        priority=[
            extension_id for extension_id in await ExtensionPriority().distributed() if entitlement.allows(extension_id)
        ],
    )


@router.get('/extensions/{extension_id}/bundle', tags=['Extensions'])
async def bundle(extension_id: str, caller: ProducerCaller = Depends(current_producer)) -> Response:
    entitlement = await GroupExtensions().of_producer(caller)
    found = await ServiceExtensionDB().archive_of(extension_id) if entitlement.allows(extension_id) else None
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No approved service ships this extension')
    return Response(
        content=found.archive,
        media_type='application/gzip',
        headers={SIGNATURE_HEADER: found.signature, 'X-Igni-Bundle-Sha256': found.sha256},
    )


@router.head('/clients/{client_id}/extension/assets/{sha256}', tags=['Identity'])
async def holds_asset(client_id: str, sha256: str) -> Response:
    """Whether igni holds this asset for this client. Public, like the
    bundle's digest: a digest says nothing the signed manifest does not."""
    if not _SHA256.match(sha256) or not await ExtensionAssets().holds(client_id, sha256):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return Response(status_code=status.HTTP_200_OK)


@router.put('/clients/{client_id}/extension/assets/{sha256}', response_model=HeldAsset, tags=['Identity'])
async def upload_asset(
    client_id: str,
    sha256: str,
    request: Request,
    signature: str = Header(..., alias=ASSET_SIGNATURE_HEADER),
) -> HeldAsset:
    if not _SHA256.match(sha256):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='An asset is named by its SHA-256')
    try:
        stored = await ExtensionAssets().upload(client_id, sha256, request.stream(), signature)
    except AssetRefused as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return HeldAsset(sha256=stored.sha256, size=stored.size)


@router.get('/extensions/{extension_id}/assets/{sha256}', tags=['Extensions'])
async def asset(
    extension_id: str,
    sha256: str,
    byte_range: str | None = Header(None, alias='Range'),
    caller: ProducerCaller = Depends(current_producer),
) -> Response:
    """One asset of a distributed extension — only one its live manifest declares — whole or one range of it."""
    entitlement = await GroupExtensions().of_producer(caller)
    found = await ServiceExtensionDB().archive_of(extension_id) if entitlement.allows(extension_id) else None
    if found is None or not BundleAssets.of(found.manifest).declares(sha256):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='No distributed extension declares this asset'
        )
    assets = ExtensionAssets()
    held = await assets.held(found.client_id, sha256)
    if held is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='igni does not hold this asset')
    headers = {'X-Igni-Asset-Sha256': held.sha256, 'ETag': f'"{held.sha256}"', 'Accept-Ranges': 'bytes'}
    span = ByteRange.of(byte_range, held.size)
    if span is None:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={**headers, 'Content-Range': f'bytes */{held.size}'},
        )
    headers['Content-Length'] = str(span.length)
    if span.partial:
        headers['Content-Range'] = f'bytes {span.start}-{span.end}/{held.size}'
    return StreamingResponse(
        assets.stream(held, span),
        status_code=status.HTTP_206_PARTIAL_CONTENT if span.partial else status.HTTP_200_OK,
        media_type='application/octet-stream',
        headers=headers,
    )
