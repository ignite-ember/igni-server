"""Signing in right after "sign out everywhere" gives a token that works.

The watermark is rounded up to the next whole second and `iat` is whole
seconds, so for up to two seconds after a withdrawal every token minted
is refused (`tests/test_withdrawn_sessions_are_refused.py` pins those
margins). Sign-in did not know: it answered 200 with a token that was
refused on its first request, forever — and INCIDENT_RESPONSE.md C1
promises a person who was signed out "sign[s] in again and get[s] a new
one". Measured against a running server: 3/3 dead tokens at delays of 0,
0.5 and 1.1 s.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.auth.fresh_sessions import FreshSessions, MAX_WAIT_SECONDS
from tests.conftest import auth_header, create_user_with_password

pytestmark = pytest.mark.anyio

_PASSWORD = 'Correct-Horse-9-battery'


async def test_a_sign_in_straight_after_a_withdrawal_works(client, db):
    user = await create_user_with_password(db, _PASSWORD, email='back@acmestraight.com')
    first = await client.post('/sign-in', json={'email': 'back@acmestraight.com', 'password': _PASSWORD})
    assert first.status_code == 200, first.text
    withdrawn = await client.post('/portal/me/revoke-sessions', headers=auth_header(first.json()['access_token']))
    assert withdrawn.status_code == 200, withdrawn.text

    again = await client.post('/sign-in', json={'email': 'back@acmestraight.com', 'password': _PASSWORD})
    assert again.status_code == 200, again.text

    me = await client.get('/portal/me', headers=auth_header(again.json()['access_token']))
    assert me.status_code == 200, f'sign-in handed out a token born withdrawn: {me.text}'
    assert me.json()['user_id'] == str(user.user_id)


class _Watermark:
    def __init__(self, when: datetime | None) -> None:
        self.when = when

    async def watermark(self, user_id: str) -> datetime | None:
        return self.when


async def _waited(when: datetime | None, now: float) -> list[float]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    await FreshSessions(sessions=_Watermark(when), clock=lambda: now, sleep=sleep).wait_until_honoured('u')
    return slept


async def test_it_waits_until_the_first_whole_second_past_the_watermark():
    watermark = datetime(2026, 11, 15, 12, 0, 1, tzinfo=timezone.utc)

    slept = await _waited(watermark, now=watermark.timestamp() - 0.6)

    assert slept == [pytest.approx(1.6)]


async def test_no_wait_without_a_watermark_or_once_it_has_passed():
    past = datetime.now(timezone.utc) - timedelta(minutes=5)

    assert await _waited(None, now=0.0) == []
    assert await _waited(past, now=datetime.now(timezone.utc).timestamp()) == []


async def test_it_never_waits_out_clock_skew():
    far = datetime.now(timezone.utc) + timedelta(seconds=MAX_WAIT_SECONDS + 5)

    assert await _waited(far, now=datetime.now(timezone.utc).timestamp()) == []
