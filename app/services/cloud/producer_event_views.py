"""A stored item as each reader sees it: its service reading what became of it, the producer's igni receiving it."""

from __future__ import annotations

from pydantic import TypeAdapter

from app.api.base_v1.schema.producer_events import (
    AttachmentRef,
    ChatAction,
    EventState,
    EventStatus,
    ItemKind,
    PendingEvent,
)
from app.db.sql.models.producer_event import ProducerEvent
from app.db.sql.models.service_attachment import ServiceAttachment
from app.security.producer import ProducerRef

_ITEM_KINDS: TypeAdapter[ItemKind] = TypeAdapter(ItemKind)
_STATES: TypeAdapter[EventState] = TypeAdapter(EventState)


class ProducerEventViews:
    """Rows into answers; the stored `kind` and `state` are checked rather than assumed."""

    @staticmethod
    def status(row: ProducerEvent) -> EventStatus:
        return EventStatus(
            event_id=row.id,
            producer=ProducerRef.stored(row.producer_kind, row.producer_id),
            kind=_ITEM_KINDS.validate_python(row.kind),
            type=row.event_type,
            thread=row.thread,
            event_key=row.event_key,
            state=_STATES.validate_python(row.state),
            dropped_reason=row.dropped_reason,
            failed_reason=row.failed_reason,
            not_handled_reason=row.not_handled_reason,
            created_at=row.created_at,
            expires_at=row.expires_at,
            delivered_at=row.delivered_at,
            updated_at=row.updated_at,
        )

    @classmethod
    def pending(cls, row: ProducerEvent, named: dict[str, ServiceAttachment]) -> PendingEvent:
        """An item as the producer's igni receives it, its attachments described, not carried."""
        return PendingEvent(
            event_id=row.id,
            extension_id=row.extension_id,
            client_id=row.client_id,
            kind=_ITEM_KINDS.validate_python(row.kind),
            type=row.event_type,
            payload=dict(row.payload or {}),
            thread=row.thread,
            text=row.text,
            title=row.title,
            start_run=row.start_run,
            action=ChatAction.model_validate(row.action) if row.action else None,
            attachments=[
                cls.attachment(held) for held in (named.get(i) for i in (row.attachment_ids or [])) if held is not None
            ],
            event_key=row.event_key,
            created_at=row.created_at,
            expires_at=row.expires_at,
        )

    @staticmethod
    def attachment(held: ServiceAttachment) -> AttachmentRef:
        return AttachmentRef(
            attachment_id=held.id,
            filename=held.filename,
            content_type=held.content_type,
            size=held.size,
            sha256=held.sha256,
        )
