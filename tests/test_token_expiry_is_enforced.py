"""The 30-day expiry, asserted rather than assumed.

B8. Until B5 there was no revocation at all, which made `exp` the only
thing bounding a leaked token — and nothing in 1900 tests ever minted an
expired one to check it was refused. The nearest thing was a string
comparison against the words "Invalid or expired token", which is the
message, not the behaviour.

`python-jose` validates `exp` by default. "By default" is exactly the
kind of assumption this review keeps finding wrong: an `options={}`
passed to `jwt.decode` for some other reason, or a move to a different
library, turns every token in the field into a permanent credential, and
the failure is invisible — everything keeps working, forever.

Leeway is checked too. A library-level grace window would silently
extend every token by its size, and a token that is expired by one
second is expired.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.services.auth.jwt_service import JWTService
from tests.conftest import auth_header, create_user

pytestmark = pytest.mark.anyio


class TestTheDecoder:
    def test_an_expired_token_does_not_decode(self):
        """`decode_token` answers all-`None` for anything it will not
        accept, and every caller checks `user_id` — so this is the
        difference between a refusal and a subject."""
        token = JWTService().create_token('somebody', expires_delta=timedelta(seconds=-1))
        assert JWTService().decode_token(token).user_id is None

    def test_a_live_token_does_decode(self):
        """The control. Without it, a decoder that rejected everything
        would pass the test above."""
        token = JWTService().create_token('somebody', expires_delta=timedelta(minutes=5))
        assert JWTService().decode_token(token).user_id == 'somebody'

    def test_there_is_no_grace_window(self):
        """Expired by a second is expired.

        A configured leeway would extend every token in the deployment by
        its own size, which is a change to the security posture made in
        a keyword argument.
        """
        token = JWTService().create_token('somebody', expires_delta=timedelta(seconds=-1))
        assert JWTService().decode_token(token).user_id is None

    def test_the_default_lifetime_is_thirty_days(self):
        """Pinned because DEC-10 left it open as a deliberate question,
        and a number nobody asserts is a number that drifts. If this
        changes, it should change because somebody decided to."""
        import jose.jwt as jose_jwt

        token = JWTService().create_token('somebody')
        claims = jose_jwt.get_unverified_claims(token)
        lifetime = claims['exp'] - claims['iat']
        assert lifetime == int(timedelta(days=30).total_seconds()), f'{lifetime}s'


class TestTheAuthenticatedSurface:
    async def test_an_expired_token_is_refused_over_the_wire(self, client, db):
        """The decoder is one thing; what the route does with it is the
        thing. The user exists, is an active member and has never been
        revoked — the only problem with the token is its age.
        """
        import uuid

        user_id = f'expired-{uuid.uuid4().hex[:8]}'
        await create_user(db, user_id=user_id, email=f'{user_id}@example.invalid', full_name='Stale')

        stale = JWTService().create_token(user_id, expires_delta=timedelta(seconds=-1))
        response = await client.get('/portal/me', headers=auth_header(stale))
        assert (
            response.status_code == 401
        ), f'an expired token was accepted on the authenticated surface: {response.status_code}'

        fresh = JWTService().create_token(user_id)
        assert (await client.get('/portal/me', headers=auth_header(fresh))).status_code == 200
