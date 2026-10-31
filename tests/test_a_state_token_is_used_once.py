"""The OAuth state token, and the two things it got wrong.

A state token proves the callback now arriving belongs to a flow this
server started. For OIDC it also carries the nonce and the PKCE
verifier — the two secrets that stop an injected authorization code
from being exchanged — so "consumed exactly once" is not hygiene, it
is the defence.

**It was not once.** `validate_and_consume` did `GET` and then
`DELETE` as separate round trips, so two callbacks carrying the same
token both read it before either deleted it. Measured against the real
Redis, not argued: eight concurrent consumers of one token, four of
them handed it.

**And a token with no metadata came back as the integer 1.** It is
stored as the sentinel `'1'`, which `json.loads` parses to `1` rather
than failing — so the `except json.JSONDecodeError` below it never
ran and the int was returned under a `dict | None` annotation. The
social sign-in callback reads `metadata.get('returnTo', '')` one line
after calling this, so signing in with GitHub, Google or GitLab
*without* a `returnTo` raised `AttributeError` and answered 500.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.db.redis.oauth_state import OAuthStateDB
from app.services.auth.oauth_service import OAuthService

pytestmark = pytest.mark.asyncio


def _token() -> str:
    return f'probe-{uuid.uuid4().hex}'


class TestExactlyOneConsumerWins:
    async def test_a_second_consumer_is_refused(self):
        state = _token()
        db = OAuthStateDB()
        await db.store(state, {'nonce': 'n-1', 'pkce': 'v-1'})

        first = await db.validate_and_consume(state)
        second = await db.validate_and_consume(state)

        assert first == {'nonce': 'n-1', 'pkce': 'v-1'}
        assert second is None

    async def test_concurrent_consumers_do_not_all_win(self):
        """The shape the two-step version actually failed in.

        Sequential reuse was already refused — the delete had landed by
        then. It took concurrency to show it, which is exactly how a
        callback race arrives: two tabs, a double-clicked link, a
        retrying proxy.
        """
        state = _token()
        db = OAuthStateDB()
        await db.store(state, {'nonce': 'n-1', 'pkce': 'v-1'})

        results = await asyncio.gather(*[db.validate_and_consume(state) for _ in range(8)])

        assert len([r for r in results if r]) == 1, results

    async def test_an_unknown_token_is_none(self):
        assert await OAuthStateDB().validate_and_consume(_token()) is None


class TestATokenWithNoMetadataIsStillADict:
    async def test_it_reads_back_as_an_empty_mapping(self):
        state = _token()
        await OAuthStateDB().store(state)

        claimed = await OAuthStateDB().validate_and_consume(state)

        assert claimed == {}
        assert isinstance(claimed, dict), f'{claimed!r} is a {type(claimed).__name__}'

    async def test_the_social_callback_can_read_it(self):
        """The failing line, through the service the endpoint uses.

        `/auth/{provider}` stores `None` when no `returnTo` is given,
        which is the only way to reach the sentinel.
        """
        state = await OAuthService.generate_state(None)

        metadata = await OAuthService.validate_state(state)

        assert metadata.get('returnTo', '') == ''

    async def test_a_token_with_metadata_still_carries_it(self):
        state = await OAuthService.generate_state({'returnTo': '/docs'})

        assert (await OAuthService.validate_state(state)).get('returnTo') == '/docs'
