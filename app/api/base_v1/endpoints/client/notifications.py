"""A person's notifications, for the igni host on their machine.

``docs/service-identity.md`` §10.1: list (which marks delivered), count
per extension, mark read, and a stream that says when to list again.

**The stream carries no notification.** It says "something is newer",
and the host answers by listing. A dropped connection is harmless: a
host that misses fifty nudges lists once on reconnect and is as correct
as one that received every one.

**A notification is only ever yours.** Every route resolves the account
from the token and scopes the query to it — not checks afterwards,
scopes.

**Every organisation you are in.** A notification lives in the database
of the organisation it happened in, and a person in two organisations
has one list: it merges both newest first (each row says its
``org_id``), the counts add up, marking read reaches both, and the
stream wakes for either. One you have left is no longer read.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Header, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.base_v1.schema.notification import (
    MarkRead,
    NotificationList,
    NotificationRead,
    NotificationRow,
)
from app.db.sql.schemas.notification import MAX_KEY, NotificationCounts
from app.security.auth import get_token
from app.security.subject import resolve_existing_user_id
from app.services.notifications import NotificationFeed, NotificationStream

router = APIRouter()


@router.get('/notifications', response_model=NotificationList, summary='Your notifications')
async def list_notifications(
    unread_only: bool = Query(default=False),
    source: str = Query(default='', max_length=MAX_KEY, description='One extension id'),
    limit: int = Query(default=NotificationFeed.MAX_PAGE, ge=1, le=NotificationFeed.MAX_PAGE),
    token: str = Depends(get_token),
    feed: NotificationFeed = Depends(NotificationFeed.default),
) -> NotificationList:
    """Newest first. What is listed here is marked delivered."""
    user_id = await resolve_existing_user_id(token)
    refs = await feed.listing(user_id=user_id, unread_only=unread_only, source=source, limit=limit)
    return NotificationList(notifications=[NotificationRow.from_ref(ref) for ref in refs])


@router.get('/notifications/counts', response_model=NotificationCounts, summary='Unread, per extension')
async def count_notifications(
    token: str = Depends(get_token),
    feed: NotificationFeed = Depends(NotificationFeed.default),
) -> NotificationCounts:
    """The badge on each extension, and on igni."""
    user_id = await resolve_existing_user_id(token)
    return await feed.counts(user_id=user_id)


@router.post(
    '/notifications/read',
    response_model=NotificationRead,
    status_code=status.HTTP_200_OK,
    summary='Mark notifications read',
)
async def mark_read(
    body: MarkRead | None = Body(default=None),
    token: str = Depends(get_token),
    feed: NotificationFeed = Depends(NotificationFeed.default),
) -> NotificationRead:
    """``{"ids": [...]}``, ``{"source": "..."}``, or ``{}`` for everything."""
    user_id = await resolve_existing_user_id(token)
    which = body or MarkRead()
    changed = await feed.mark_read(user_id=user_id, ids=which.ids, source=which.source)
    counts = await feed.counts(user_id=user_id)
    return NotificationRead(marked=changed, unread=counts.unread)


@router.get('/notifications/stream', summary='Wake up when something concerns you')
async def stream_notifications(
    request: Request,
    token: str = Depends(get_token),
    last_event_id: str | None = Header(default=None, alias='Last-Event-ID'),
    stream: NotificationStream = Depends(NotificationStream.default),
):
    """Server-sent events saying "there is something newer for you".

    Lossy, in-process, and carrying a cursor rather than contents; the
    list is what is true.
    """
    user_id = await resolve_existing_user_id(token)

    async def events():
        with stream.listen(user_id=user_id, cursor=last_event_id or '') as channel:
            yield channel.opening()
            while not await request.is_disconnected():
                if channel.spent():
                    yield channel.reconnect()
                    return
                yield await channel.waiting()

    return StreamingResponse(events(), media_type='text/event-stream', headers=stream.headers)
