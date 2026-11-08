"""Pydantic schemas for what a service sends a producer (`docs/service-identity.md` §15).

An **item** is one of four kinds, on two paths. An `event` is for the
service's extension: igni hands it over and posts nothing, and an event
no handler took comes back `not_handled`. A `message`, a new `chat` or an
`action` on a chat is for the agent, and igni posts it exactly as sent.
Every kind travels the same way: stored until delivered, handed out
under a lease, acknowledged, expired after at most 14 days.
`POST /v1/service-events` is the event-only form; `POST /v1/service-items`
takes any kind.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.security.producer import ProducerRef
from app.services.identity.impact.grants import ServiceGrant

#: The largest payload, as JSON.
MAX_PAYLOAD_BYTES = 65536
#: The longest message text, in characters.
MAX_TEXT_CHARS = 65536
#: The largest view an action shows, as JSON.
MAX_VIEW_BYTES = 65536
#: Attachments one item may name.
MAX_ATTACHMENTS = 10
#: The largest attachment: what a person may attach in igni's composer.
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
#: How long an attachment no item names is kept.
UNNAMED_ATTACHMENT_SECONDS = 24 * 60 * 60

ItemKind = Literal['event', 'message', 'chat', 'action']
ActionOp = Literal['show', 'rename', 'cancel']


class ChatAction(BaseModel):
    """What an `action` item does to its thread's chat."""

    op: ActionOp
    #: `show`: a json-render spec, shown in the chat and never sent to the model.
    view: dict[str, object] | None = None
    #: `rename`: the chat's new title; `show`: the view's title.
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode='after')
    def _what_each_op_needs(self) -> 'ChatAction':
        if self.op == 'show' and not isinstance((self.view or {}).get('root'), str):
            raise ValueError('show needs a json-render view: {root, elements}')
        if self.op == 'rename' and not self.title:
            raise ValueError('rename needs a title')
        if self.op != 'show' and self.view is not None:
            raise ValueError(f'{self.op} takes no view')
        return self


class DeliverItemRequest(BaseModel):
    """A service asking igni to deliver one item to its extension for one producer."""

    producer: ProducerRef
    kind: ItemKind
    #: An event's type, the extension's own word: `index.ready`.
    type: str | None = Field(default=None, min_length=1, max_length=100)
    #: An event's payload.
    payload: dict[str, object] = Field(default_factory=dict)
    #: The service's key for a conversation, `claim:CL-20431`. The first
    #: item of a thread opens a chat for it and every later one goes
    #: there; without one, the item goes to the extension's own chat.
    #: Not for an event, which posts nothing.
    thread: str | None = Field(default=None, min_length=1, max_length=200)
    #: A message's text, or a new chat's first message.
    text: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT_CHARS)
    #: A new chat's title, or the title of the chat a thread's first item opens.
    title: str | None = Field(default=None, min_length=1, max_length=200)
    #: A message: start a run (or wait for the run going); false waits for the chat's next run.
    start_run: bool = True
    action: ChatAction | None = None
    #: Ids of attachments this service uploaded (`POST /v1/service-attachments`).
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    #: The same key for one producer is stored once; a retry is answered
    #: with the item already stored.
    event_key: str | None = Field(default=None, min_length=1, max_length=200)
    #: Do not deliver after this. At most `SERVICE_EVENT_MAX_WAIT_SECONDS`
    #: (14 days) from now, which is also what an item without one gets;
    #: a later time is cut to it, and the answer says what was kept.
    expires_at: datetime | None = None

    @model_validator(mode='after')
    def _what_each_kind_needs(self) -> 'DeliverItemRequest':
        problems = {
            'event': [
                (not self.type, 'an event needs a type'),
                (
                    self.text is not None or self.thread is not None or bool(self.attachments),
                    "an event takes no text, thread or attachments: it is its extension's, and posts nothing",
                ),
            ],
            'message': [(not self.text, 'a message needs text'), (self.type is not None, 'a message takes no type')],
            'chat': [
                (not self.thread, 'a chat needs a thread'),
                (not self.title, 'a chat needs a title'),
                (self.type is not None, 'a chat takes no type'),
            ],
            'action': [
                (self.action is None, 'an action needs an action'),
                (self.type is not None or self.text is not None, 'an action takes no type or text'),
            ],
        }[self.kind]
        problems.append((self.kind != 'action' and self.action is not None, f'a {self.kind} takes no action'))
        problems.append((self.kind != 'event' and bool(self.payload), f'a {self.kind} takes no payload'))
        problems.append((len(set(self.attachments)) != len(self.attachments), 'an attachment is named twice'))
        for failed, reason in problems:
            if failed:
                raise ValueError(reason)
        return self

    def needs(self) -> set[ServiceGrant]:
        """The grants this item needs beyond a plain event."""
        needed: set[ServiceGrant] = set()
        if self.kind == 'message':
            needed.add(ServiceGrant.MESSAGES)
        if self.kind in ('chat', 'action') or self.thread is not None:
            needed.add(ServiceGrant.CHATS)
        if self.attachments:
            needed.add(ServiceGrant.ATTACHMENTS)
        return needed

    @property
    def stored_type(self) -> str:
        """An event's type; for any other kind, the kind."""
        return self.type or self.kind


class DeliverEventRequest(BaseModel):
    """`POST /v1/service-events`: an event, the item every service may send."""

    producer: ProducerRef
    #: The extension's own word for what happened: `index.ready`.
    type: str = Field(min_length=1, max_length=100)
    payload: dict[str, object] = Field(default_factory=dict)
    #: The same key for one producer is stored once; a retry is answered
    #: with the event already stored.
    event_key: str | None = Field(default=None, min_length=1, max_length=200)
    #: Do not deliver after this. At most `SERVICE_EVENT_MAX_WAIT_SECONDS`
    #: (14 days) from now, which is also what an event without one gets;
    #: a later time is cut to it, and the answer says what was kept.
    expires_at: datetime | None = None

    def item(self) -> DeliverItemRequest:
        return DeliverItemRequest(kind='event', **self.model_dump())


class EventAccepted(BaseModel):
    event_id: str
    #: When it expires if undelivered — never later than 14 days from now.
    expires_at: datetime | None
    #: True when `event_key` matched an item already stored.
    duplicate: bool = False


class AttachmentRef(BaseModel):
    """An attachment as a producer's igni is told of it."""

    attachment_id: str
    filename: str
    content_type: str
    size: int
    #: Hex SHA-256 of the bytes.
    sha256: str


class AttachmentStored(AttachmentRef):
    """`POST /v1/service-attachments`, answered."""

    #: Gone after this: a day while no item names it, then the item's expiry.
    expires_at: datetime


class PendingEvent(BaseModel):
    """An item as the gateway, or igni on a desktop, hands it to the producer's igni."""

    event_id: str
    extension_id: str
    #: The service that sent it.
    client_id: str
    kind: ItemKind = 'event'
    #: An event's type; for any other kind, the kind.
    type: str
    payload: dict[str, object]
    thread: str | None = None
    text: str | None = None
    title: str | None = None
    start_run: bool = True
    action: ChatAction | None = None
    #: Fetched with the producer's own credential:
    #: `GET /v1/me/events/{event_id}/attachments/{attachment_id}`.
    attachments: list[AttachmentRef] = Field(default_factory=list)
    event_key: str | None
    created_at: datetime
    expires_at: datetime | None


class PulledEvents(BaseModel):
    events: list[PendingEvent]


class NotHandledEvent(BaseModel):
    """An event the producer's igni handed to its extension, and no handler took."""

    event_id: str
    #: Why: no handler for its type, the extension not running, the handler failing.
    reason: str = Field(min_length=1, max_length=500)


class AckEvents(BaseModel):
    event_ids: list[str] = Field(min_length=1, max_length=500)
    #: Of `event_ids`, the events no handler took: `not_handled`, not `delivered`.
    not_handled: list[NotHandledEvent] = Field(default_factory=list, max_length=500)

    def not_handled_reasons(self) -> dict[str, str]:
        """Each event no handler took, by its id, with the reason."""
        return {missed.event_id: missed.reason for missed in self.not_handled}


class FailEvents(BaseModel):
    event_ids: list[str] = Field(min_length=1, max_length=500)
    #: Why the gateway gave up — what the service reads.
    reason: str = Field(min_length=1, max_length=500)


class FailedEvents(BaseModel):
    #: How many of them were pending and are now failed.
    failed: int


class AckedEvents(BaseModel):
    #: How many of them were pending and are now delivered.
    delivered: int


EventState = Literal['pending', 'delivered', 'not_handled', 'expired', 'dropped', 'failed']


class EventStatus(BaseModel):
    """What became of an item, as the service that sent it reads it."""

    event_id: str
    producer: ProducerRef
    kind: ItemKind = 'event'
    type: str
    thread: str | None = None
    event_key: str | None
    state: EventState
    #: Why it was dropped: `extension_switched_off`, `producer_removed`.
    dropped_reason: str | None
    #: Why the gateway gave up delivering it.
    failed_reason: str | None = None
    #: An event no handler took: why.
    not_handled_reason: str | None = None
    created_at: datetime
    expires_at: datetime | None
    delivered_at: datetime | None
    #: When `state` last changed — poll with `since` set to the last one seen.
    updated_at: datetime


class EventStatusList(BaseModel):
    events: list[EventStatus]
