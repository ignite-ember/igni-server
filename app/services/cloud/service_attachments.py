"""Files a service sends with its items, and the producer's igni fetching them (`docs/service-identity.md` §15).

1. The service uploads the bytes (`POST /v1/service-attachments`), with
   the `attachments` grant. igni server keeps them in the organisation's
   own database, a day at most while no item names them.
2. An item names them by id; they become that item's, and expire with it.
3. The producer's igni, delivering the item, fetches each with its own
   credential — the person's session, or the VM's token — and imports it
   into the chat as a person's upload would be.

Any type a person may attach in igni's composer may be sent, up to the
composer's 5 MiB. The name must be a plain file name, and the type a
media type.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
import uuid

from fastapi import HTTPException, status

from app.api.base_v1.schema.producer_events import (
    AttachmentStored,
    MAX_ATTACHMENT_BYTES,
    UNNAMED_ATTACHMENT_SECONDS,
)
from app.db.sql.models.service_attachment import ServiceAttachment
from app.db.sql.service_attachments import ServiceAttachmentsDB
from app.security.producer import ProducerCaller
from app.security.service_caller import ServiceCaller
from app.services.cloud.event_rate_limit import EventRateLimit
from app.services.cloud.service_grants import ServiceExtensionOf, ServiceGrants
from app.services.identity.impact import ServiceGrant


class AttachmentName:
    """A file name and a media type as an upload states them, checked."""

    #: `type/subtype`, RFC 6838's characters, parameters dropped.
    MEDIA_TYPE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}$')
    MAX_NAME = 200

    @classmethod
    def filename(cls, raw: str) -> str:
        name = (raw or '').strip()
        if not name or len(name) > cls.MAX_NAME or name in ('.', '..'):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='filename must be 1-200 characters')
        if any(c in name for c in '/\\\x00') or any(ord(c) < 32 for c in name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail='filename must be a file name, not a path'
            )
        return name

    @classmethod
    def content_type(cls, raw: str | None) -> str:
        media = (raw or 'application/octet-stream').split(';', 1)[0].strip().lower()
        if not cls.MEDIA_TYPE.match(media):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=f'{media!r} is not a media type'
            )
        return media


class ServiceAttachments:
    def __init__(self, db: ServiceAttachmentsDB | None = None, *, rate_limit: EventRateLimit | None = None) -> None:
        self._db = db if db is not None else ServiceAttachmentsDB()
        self._rate_limit = rate_limit or EventRateLimit()

    async def upload(
        self, caller: ServiceCaller, *, filename: str, content_type: str | None, content: bytes
    ) -> AttachmentStored:
        name = AttachmentName.filename(filename)
        media = AttachmentName.content_type(content_type)
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='an attachment has no bytes')
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f'An attachment may be at most {MAX_ATTACHMENT_BYTES} bytes',
            )
        await self._rate_limit.spend(caller.client_id)
        ServiceGrants.require(await ServiceExtensionOf.caller(caller), {ServiceGrant.ATTACHMENTS})
        stored = await self._db.put(
            caller.org_id,
            ServiceAttachment(
                id=str(uuid.uuid4()),
                client_id=caller.client_id,
                filename=name,
                content_type=media,
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                content=content,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=UNNAMED_ATTACHMENT_SECONDS),
            ),
        )
        return AttachmentStored(
            attachment_id=stored.id,
            filename=stored.filename,
            content_type=stored.content_type,
            size=stored.size,
            sha256=stored.sha256,
            expires_at=stored.expires_at,
        )

    async def for_producer(self, caller: ProducerCaller, *, event_id: str, attachment_id: str) -> ServiceAttachment:
        """One attachment of one of the caller's own items; anybody else's is a 404."""
        if not caller.org_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such attachment')
        held = await self._db.for_producer(
            caller.org_id,
            event_id=event_id,
            attachment_id=attachment_id,
            producer_kind=caller.kind,
            producer_id=caller.id,
        )
        if held is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such attachment')
        return held
