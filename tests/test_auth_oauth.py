"""Tests for OAuth flows: redirect, callback, and find_or_create_user.

These are about the mechanics of a provider round trip, not about
which providers a deployment chooses to offer, so each test gives
Google credentials, which is what a deployment offering it would do.
"""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db.sql.models.org_membership import OrgMembership
from app.services.auth.google_sign_in import GoogleSignIn
from app.services.auth.oauth_errors import OAuthExchangeFailed, ProviderAnswerRefused
from app.services.auth.oauth_models import OAuthProvider, OAuthUserInfo
from app.services.auth.oauth_service import OAuthService
from app.services.email.delivery import EmailDelivery
from app.services.email.service import EmailService
from tests.conftest import add_invited_member, create_org, create_user


@pytest.fixture(autouse=True)
def _google_sign_in_enabled(monkeypatch):
    """A deployment that asked for Google sign-in and configured it."""
    monkeypatch.setattr(settings, 'oauth_providers', ['google'])
    monkeypatch.setattr(settings, 'oauth_google_client_id', 'google-client-id')
    monkeypatch.setattr(settings, 'oauth_google_client_secret', 'google-client-secret')


class TestOAuthRedirect:
    async def test_google_redirect(self, client, monkeypatch):
        async def fake_generate_state(record):
            return 'fake-state-token'

        monkeypatch.setattr(OAuthService, 'generate_state', staticmethod(fake_generate_state))

        resp = await client.get('/auth/google', follow_redirects=False)

        assert resp.status_code == 302
        location = resp.headers['location']
        assert 'accounts.google.com' in location
        assert 'fake-state-token' in location


class TestAProviderThatCannotBeUsedIsRefusedHere:
    """Before the person is sent anywhere: the reason is known here, and
    at the provider it would be an error about a request this server built."""

    async def test_one_that_does_not_exist(self, client):
        resp = await client.get('/auth/myspace', follow_redirects=False)

        assert resp.status_code == 400
        assert 'Unsupported provider' in resp.text

    async def test_one_the_deployment_did_not_enable(self, client, monkeypatch):
        monkeypatch.setattr(settings, 'oauth_providers', [])

        resp = await client.get('/auth/google', follow_redirects=False)

        assert resp.status_code == 400
        assert 'not enabled' in resp.text

    async def test_one_enabled_without_credentials(self, client, monkeypatch):
        """It redirected to Google with `client_id=` before, and the person
        read Google's error about it."""
        monkeypatch.setattr(settings, 'oauth_google_client_id', '')
        monkeypatch.setattr(settings, 'oauth_google_client_secret', '')

        resp = await client.get('/auth/google', follow_redirects=False)

        assert resp.status_code == 400
        assert 'no credentials' in resp.text


class TestOAuthCallback:
    async def test_google_callback_new_user(self, client, monkeypatch):
        async def fake_validate_state(state, shape):
            return shape()

        async def fake_exchange(provider, code):
            return OAuthUserInfo(
                email='oauth-user@test.com',
                first_name='OAuth',
                last_name='User',
                provider='google',
            )

        monkeypatch.setattr(OAuthService, 'validate_state', staticmethod(fake_validate_state))
        monkeypatch.setattr(OAuthService, 'exchange_and_get_user', staticmethod(fake_exchange))

        resp = await client.get(
            '/auth/google/callback?code=test-code&state=test-state',
            follow_redirects=False,
        )

        assert resp.status_code == 302
        location = resp.headers['location']

        # A one-time code, not the token. F107 changed this contract: a
        # `?token=<jwt>` redirect put a working credential in the browser
        # history, in the referrer of anything the page touched, and in
        # the portal proxy's access log.
        assert 'code=' in location, location
        assert 'token=' not in location, location

        # And the code is worth something exactly once. Asserted by
        # redeeming it, because "there is a code in the URL" would pass
        # against a code that stands for nothing.
        code = parse_qs(urlparse(location).query)['code'][0]

        first = await client.post('/auth/exchange', json={'code': code})
        assert first.status_code == 200, first.text
        assert first.json()['access_token'], 'the code redeemed to an empty token'

        second = await client.post('/auth/exchange', json={'code': code})
        assert second.status_code == 400, 'the code was accepted twice'

    async def test_oauth_new_user_claims_invites(self, client, db, verify_db, monkeypatch):
        # Setup: create org with a pending invite for the OAuth user's email
        await create_user(db, user_id='owner-1', email='owner@test.com')
        org = await create_org(db, owner_user_id='owner-1', slug='oauth-org', domains=('test.com',))
        invited = await add_invited_member(
            db,
            org_id=str(org.id),
            email='oauth-invited@test.com',
            role='member',
            invited_by='owner-1',
        )
        invited_id = str(invited.id)

        async def fake_validate_state(state, shape):
            return shape()

        async def fake_exchange(provider, code):
            return OAuthUserInfo(
                email='oauth-invited@test.com',
                first_name='Invited',
                last_name='User',
                provider='google',
            )

        monkeypatch.setattr(OAuthService, 'validate_state', staticmethod(fake_validate_state))
        monkeypatch.setattr(OAuthService, 'exchange_and_get_user', staticmethod(fake_exchange))

        resp = await client.get(
            '/auth/google/callback?code=test-code&state=test-state',
            follow_redirects=False,
        )

        assert resp.status_code == 302

        # Verify via fresh session (app singletons committed in their own sessions)
        result = await verify_db.execute(select(OrgMembership).where(OrgMembership.id == invited_id))
        membership = result.scalars().first()
        assert membership is not None
        assert membership.status == 'active'
        assert not str(membership.user_id).startswith('invited-')


def _signs_in_as(monkeypatch, email: str, *, state: dict | None = None) -> None:
    async def fake_validate_state(_state, shape):
        return shape.model_validate(state or {})

    async def fake_exchange(provider, code):
        return OAuthUserInfo(email=email, first_name='Ada', last_name='L', provider='google')

    monkeypatch.setattr(OAuthService, 'validate_state', staticmethod(fake_validate_state))
    monkeypatch.setattr(OAuthService, 'exchange_and_get_user', staticmethod(fake_exchange))


class TestWhatTheCallbackDoesAroundTheSignIn:
    async def test_where_the_person_was_goes_back_with_them(self, client, monkeypatch):
        """A sign-in that started on `/authorize?...` or `/cli-auth?...`
        carries that path through the provider in the state, and the
        portal is handed it back to resume."""
        _signs_in_as(monkeypatch, 'returning@test.com', state={'returnTo': '/cli-auth?port=7123'})

        resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        query = parse_qs(urlparse(resp.headers['location']).query)
        assert query['returnTo'] == ['/cli-auth?port=7123']
        assert query['code']

    async def test_without_one_the_portal_is_given_none(self, client, monkeypatch):
        _signs_in_as(monkeypatch, 'plain@test.com')

        resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert 'returnTo' not in parse_qs(urlparse(resp.headers['location']).query)

    async def test_a_new_person_is_welcomed_when_mail_can_go(self, client, monkeypatch):
        sent: list[str] = []

        async def send_welcome(self, *, to_email: str, name: str | None = None) -> None:
            sent.append(to_email)

        monkeypatch.setattr(EmailDelivery, 'enabled', property(lambda self: True))
        monkeypatch.setattr(EmailService, 'send_welcome', send_welcome)
        _signs_in_as(monkeypatch, 'brand-new@test.com')

        resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert resp.status_code == 302
        assert sent == ['brand-new@test.com']

    async def test_someone_signing_in_again_is_not_welcomed_again(self, client, db, monkeypatch):
        await create_user(db, user_id='known', email='known@test.com')
        sent: list[str] = []

        async def send_welcome(self, *, to_email: str, name: str | None = None) -> None:
            sent.append(to_email)

        monkeypatch.setattr(EmailDelivery, 'enabled', property(lambda self: True))
        monkeypatch.setattr(EmailService, 'send_welcome', send_welcome)
        _signs_in_as(monkeypatch, 'known@test.com')

        await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert sent == []

    async def test_a_provider_that_fails_sends_the_person_back_to_sign_in_with_a_reason(self, client, monkeypatch):
        """Not a 500 page on the API's host: the portal's sign-in form,
        which shows the error, and no code."""

        async def fake_validate_state(_state, shape):
            return shape()

        async def broken_exchange(provider, code):
            raise OAuthExchangeFailed(provider) from RuntimeError('upstream said: invalid_grant')

        monkeypatch.setattr(OAuthService, 'validate_state', staticmethod(fake_validate_state))
        monkeypatch.setattr(OAuthService, 'exchange_and_get_user', staticmethod(broken_exchange))

        resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert resp.status_code == 302
        location = urlparse(resp.headers['location'])
        assert location.path == '/login'
        query = parse_qs(location.query)
        assert query['error'][0].startswith('OAuth sign-in failed')
        assert 'code' not in query
        assert 'invalid_grant' not in query['error'][0], 'the upstream reason reached the URL'

    async def test_a_failure_after_the_exchange_sends_the_person_back_to_sign_in_too(self, client, monkeypatch):
        """The provider answered, and then something on this side broke —
        here, the handoff store. The person still lands on the portal's
        sign-in form with a reason, not on a 500 page on the API's host,
        and what broke stays in the log rather than in the URL."""
        _signs_in_as(monkeypatch, 'store-down@test.com', state={'returnTo': '/cli-auth?port=7123'})

        async def store_down(self, token: str) -> str:
            raise ConnectionError('redis at 10.0.0.9:6379 refused the connection')

        monkeypatch.setattr('app.db.redis.auth_handoff.AuthHandoffDB.issue', store_down)

        resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert resp.status_code == 302
        location = urlparse(resp.headers['location'])
        assert location.path == '/login'
        query = parse_qs(location.query)
        assert query['error'] == ['OAuth sign-in failed. Please try again.']
        assert 'code' not in query
        assert '10.0.0.9' not in resp.headers['location'], 'the internal reason reached the URL'

    async def test_a_transport_failure_is_an_exchange_failure(self):
        """What the callback catches is named by the service, so the
        router does not need to know which HTTP client it uses."""

        class Unreachable(GoogleSignIn):
            async def exchange(self, code: str) -> OAuthUserInfo:
                raise httpx.ConnectError('no route to host')

        with pytest.raises(OAuthExchangeFailed) as caught:
            await OAuthService(google=Unreachable()).exchange_and_get_user(OAuthProvider.GOOGLE, 'c')

        assert isinstance(caught.value.__cause__, httpx.ConnectError)

    async def test_a_refusal_the_server_raises_is_not_turned_into_a_redirect(self, client, monkeypatch):
        """A refusal from the sign-in itself — an answer the server
        will not take — keeps its status rather than being folded into
        the generic failure redirect."""

        async def fake_validate_state(_state, shape):
            return shape()

        async def refusing_exchange(provider, code):
            raise ProviderAnswerRefused('Could not retrieve email from Google')

        monkeypatch.setattr(OAuthService, 'validate_state', staticmethod(fake_validate_state))
        monkeypatch.setattr(OAuthService, 'exchange_and_get_user', staticmethod(refusing_exchange))

        resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert resp.status_code == 400
        assert 'Could not retrieve email' in resp.text


class TestFindOrCreateUser:
    async def test_creates_new_user(self, db, monkeypatch):
        user_info = OAuthUserInfo(
            email='brand-new@test.com',
            first_name='Brand',
            last_name='New',
            provider='google',
        )

        result = await OAuthService().find_or_create_user_and_get_token(user_info)

        assert result.is_new_user is True
        assert result.email == 'brand-new@test.com'
        assert result.token.access_token

    async def test_returns_existing_user(self, db):
        await create_user(db, email='existing-oauth@test.com', auth_provider='google')

        user_info = OAuthUserInfo(
            email='existing-oauth@test.com',
            first_name='Existing',
            last_name='User',
            provider='google',
        )

        result = await OAuthService().find_or_create_user_and_get_token(user_info)

        assert result.is_new_user is False
        assert result.email == 'existing-oauth@test.com'
        assert result.token.access_token
