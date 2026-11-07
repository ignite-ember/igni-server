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

**And a token with no metadata came back as the integer 1**, through a
`'1'` sentinel that `json.loads` parsed rather than refused, so the
social sign-in callback raised `AttributeError` on a sign-in without a
`returnTo`. The store now holds a typed record for every token, and
one with nothing in it reads back as that record, empty.
"""

from __future__ import annotations

import asyncio
import uuid

from pydantic import ValidationError
import pytest

from app.api.base_v1.schema.oauth import SocialSignInState
from app.api.base_v1.schema.oidc import OidcSignInState
from app.db.redis.oauth_state import OAuthStateDB
from app.services.auth.oauth_service import OAuthService

pytestmark = pytest.mark.asyncio


def _token() -> str:
    return f'probe-{uuid.uuid4().hex}'


class TestExactlyOneConsumerWins:
    async def test_a_second_consumer_is_refused(self):
        state = _token()
        db = OAuthStateDB()
        signin = OidcSignInState(nonce='n-1', pkce='v-1')
        await db.store(state, signin)

        first = await db.claim(state, OidcSignInState)
        second = await db.claim(state, OidcSignInState)

        assert first == signin
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
        await db.store(state, OidcSignInState(nonce='n-1', pkce='v-1'))

        results = await asyncio.gather(*[db.claim(state, OidcSignInState) for _ in range(8)])

        assert len([r for r in results if r]) == 1, results

    async def test_an_unknown_token_is_none(self):
        assert await OAuthStateDB().claim(_token(), SocialSignInState) is None


class TestARecordWithNothingInItIsStillARecord:
    async def test_it_reads_back_empty(self):
        state = _token()
        await OAuthStateDB().store(state, SocialSignInState())

        claimed = await OAuthStateDB().claim(state, SocialSignInState)

        assert claimed == SocialSignInState()

    async def test_the_social_callback_can_read_it(self):
        """Through the service the endpoint uses: `/auth/{provider}`
        with no `returnTo` stores an empty record."""
        state = await OAuthService.generate_state(SocialSignInState())

        signin = await OAuthService.validate_state(state, SocialSignInState)

        assert signin.return_to == ''

    async def test_a_record_with_a_value_still_carries_it(self):
        state = await OAuthService.generate_state(SocialSignInState(returnTo='/docs'))

        assert (await OAuthService.validate_state(state, SocialSignInState)).return_to == '/docs'


class TestAnotherFlowsRecordIsNotThisFlows:
    async def test_it_is_refused_rather_than_read_as_empty(self):
        """A record that does not fit the shape asked for is an error the
        caller handles, never a silently empty answer."""
        state = _token()
        await OAuthStateDB().store(state, SocialSignInState(returnTo='/docs'))

        with pytest.raises(ValidationError):
            await OAuthStateDB().claim(state, OidcSignInState)
