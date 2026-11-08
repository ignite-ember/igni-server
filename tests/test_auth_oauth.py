"""Tests for OAuth flows: redirect, callback, and find_or_create_user.

These are about the mechanics of a provider round trip, not about
which providers a deployment chooses to offer, so each test gives
Google credentials, which is what a deployment offering it would do.
"""

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.user import UserDB
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

    @pytest.mark.parametrize('sent', ['/auth/google?returnTo=/docs', '/auth/google?returnTo=', '/auth/google'])
    async def test_the_state_carries_returnto_through_the_provider_and_an_empty_one_is_none(
        self, client, monkeypatch, sent
    ):
        """Through the real state store: what `/auth/google` was given
        comes back to the callback, and an empty `returnTo` is the same
        as none rather than an empty one handed to the portal."""

        async def fake_exchange(provider, code):
            return OAuthUserInfo(email='round-trip@test.com', first_name='R', last_name='T', provider='google')

        monkeypatch.setattr(OAuthService, 'exchange_and_get_user', staticmethod(fake_exchange))

        started = await client.get(sent, follow_redirects=False)
        state = parse_qs(urlparse(started.headers['location']).query)['state'][0]
        resp = await client.get(f'/auth/google/callback?code=c&state={state}', follow_redirects=False)

        query = parse_qs(urlparse(resp.headers['location']).query)
        assert query.get('returnTo') == (['/docs'] if sent.endswith('/docs') else None)
        assert query['code']

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
        assert resp.headers['location'].startswith(f'{settings.oauth_frontend_base_url}/login?')
        location = urlparse(resp.headers['location'])
        assert location.path == '/login'
        query = parse_qs(location.query)
        assert query['error'] == ['OAuth sign-in failed. Please try again.']
        assert 'code' not in query
        assert 'invalid_grant' not in resp.headers['location'], 'the upstream reason reached the URL'

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

    async def test_google_refusing_the_code_sends_the_person_back_without_its_words(self, client, monkeypatch):
        """The whole exchange, against a Google that answers 400. Its
        error body is what `str(e)` used to put in the URL."""
        _google_answers(monkeypatch, token=httpx.Response(400, json={'error': 'invalid_grant'}))

        async def fake_validate_state(_state, shape):
            return shape()

        monkeypatch.setattr(OAuthService, 'validate_state', staticmethod(fake_validate_state))

        resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert resp.status_code == 302
        query = parse_qs(urlparse(resp.headers['location']).query)
        assert query == {'error': ['OAuth sign-in failed. Please try again.']}
        assert 'invalid_grant' not in resp.headers['location']
        assert 'googleapis' not in resp.headers['location']

    async def test_an_exchange_that_breaks_in_an_unforeseen_way_sends_the_person_back_too(self, client, monkeypatch):
        """Not every failure in the exchange is one the service names.
        Whatever it is, the person lands on the sign-in form."""

        async def fake_validate_state(_state, shape):
            return shape()

        async def surprising_exchange(self, code: str) -> OAuthUserInfo:
            raise AttributeError("'list' object has no attribute 'get'")

        monkeypatch.setattr(OAuthService, 'validate_state', staticmethod(fake_validate_state))
        monkeypatch.setattr(GoogleSignIn, 'exchange', surprising_exchange)

        resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert resp.status_code == 302
        location = urlparse(resp.headers['location'])
        assert location.path == '/login'
        assert parse_qs(location.query) == {'error': ['OAuth sign-in failed. Please try again.']}
        assert 'attribute' not in resp.headers['location']

    async def test_a_database_failure_while_signing_in_sends_the_person_back_to_sign_in(self, client, monkeypatch):
        _signs_in_as(monkeypatch, 'db-down@test.com')

        async def database_down(self, email: str):
            raise OperationalError('SELECT users', {}, Exception('could not connect to 10.0.0.5:5432'))

        monkeypatch.setattr(UserDB, 'get_by_email', database_down)

        resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert resp.status_code == 302
        location = urlparse(resp.headers['location'])
        assert location.path == '/login'
        assert parse_qs(location.query) == {'error': ['OAuth sign-in failed. Please try again.']}
        assert '10.0.0.5' not in resp.headers['location'], 'the internal reason reached the URL'

    async def test_a_new_person_is_still_welcomed_when_the_handoff_store_then_fails(self, client, monkeypatch):
        """The account exists by then, so the welcome is owed; the
        person signs in again from the form they are sent back to."""
        sent: list[str] = []

        async def send_welcome(self, *, to_email: str, name: str | None = None) -> None:
            sent.append(to_email)

        async def store_down(self, token: str) -> str:
            raise ConnectionError('redis refused the connection')

        monkeypatch.setattr(EmailDelivery, 'enabled', property(lambda self: True))
        monkeypatch.setattr(EmailService, 'send_welcome', send_welcome)
        monkeypatch.setattr('app.db.redis.auth_handoff.AuthHandoffDB.issue', store_down)
        _signs_in_as(monkeypatch, 'welcomed-anyway@test.com')

        resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert resp.status_code == 302
        assert urlparse(resp.headers['location']).path == '/login'
        assert sent == ['welcomed-anyway@test.com']

    async def test_with_no_relay_a_new_person_is_only_noted(self, client, monkeypatch, caplog):
        _signs_in_as(monkeypatch, 'no-relay@test.com')

        with caplog.at_level('INFO', logger='app.services.email.delivery'):
            resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert resp.status_code == 302
        assert 'code' in parse_qs(urlparse(resp.headers['location']).query)
        assert 'skipped the welcome message to no-relay@test.com' in caplog.text

    async def test_somebody_who_left_is_refused_with_their_reason_not_redirected(self, client, db, monkeypatch):
        user = await create_user(db, email='departed@test.com')
        user.deactivated_at = datetime.now(timezone.utc)
        await db.commit()
        _signs_in_as(monkeypatch, 'departed@test.com')

        resp = await client.get('/auth/google/callback?code=c&state=s', follow_redirects=False)

        assert resp.status_code == 403
        assert resp.json() == {'detail': 'This account has been deactivated.'}

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


def _google_answers(
    monkeypatch,
    *,
    token: httpx.Response,
    profile: httpx.Response | None = None,
) -> None:
    """Google's token and userinfo endpoints, answering as given.

    Only a client built without a transport of its own is pointed at
    them, so the test client keeps talking to the app."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == 'oauth2.googleapis.com':
            return token
        return profile or httpx.Response(200, json={'email': 'someone@test.com'})

    real_client = httpx.AsyncClient

    def client_for_google(*args, **kwargs):
        kwargs.setdefault('transport', httpx.MockTransport(answer))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', client_for_google)


class TestWhatTheExchangeMakesOfGooglesAnswer:
    """`exchange_and_get_user` against Google's real client code: what
    it names an exchange failure, and what it lets through."""

    async def _exchange(self) -> OAuthUserInfo:
        return await OAuthService().exchange_and_get_user(OAuthProvider.GOOGLE, 'a-code')

    async def test_a_code_google_refuses_is_an_exchange_failure(self, monkeypatch):
        _google_answers(monkeypatch, token=httpx.Response(400, json={'error': 'invalid_grant'}))

        with pytest.raises(OAuthExchangeFailed) as caught:
            await self._exchange()

        assert isinstance(caught.value.__cause__, httpx.HTTPStatusError)
        assert str(caught.value) == 'The google sign-in exchange failed'
        assert 'invalid_grant' not in str(caught.value)

    async def test_an_answer_that_is_not_json_is_an_exchange_failure(self, monkeypatch):
        _google_answers(monkeypatch, token=httpx.Response(200, text='<html>Bad Gateway</html>'))

        with pytest.raises(OAuthExchangeFailed) as caught:
            await self._exchange()

        assert isinstance(caught.value.__cause__, ValueError)

    async def test_json_that_is_not_an_object_is_an_exchange_failure(self, monkeypatch):
        _google_answers(monkeypatch, token=httpx.Response(200, json=['access_token', 'a-token']))

        with pytest.raises(OAuthExchangeFailed):
            await self._exchange()

    async def test_a_profile_google_will_not_give_is_an_exchange_failure(self, monkeypatch):
        _google_answers(
            monkeypatch,
            token=httpx.Response(200, json={'access_token': 'a-token'}),
            profile=httpx.Response(401, json={'error': 'invalid_token'}),
        )

        with pytest.raises(OAuthExchangeFailed):
            await self._exchange()

    async def test_an_answer_without_a_token_is_refused_not_failed(self, monkeypatch):
        _google_answers(monkeypatch, token=httpx.Response(200, json={'token_type': 'Bearer'}))

        with pytest.raises(ProviderAnswerRefused, match='^Google OAuth failed$'):
            await self._exchange()

    async def test_a_profile_without_an_address_is_refused_not_failed(self, monkeypatch):
        _google_answers(
            monkeypatch,
            token=httpx.Response(200, json={'access_token': 'a-token'}),
            profile=httpx.Response(200, json={'given_name': 'Ada'}),
        )

        with pytest.raises(ProviderAnswerRefused, match='^Could not retrieve email from Google$'):
            await self._exchange()

    async def test_a_good_answer_names_the_person(self, monkeypatch):
        _google_answers(
            monkeypatch,
            token=httpx.Response(200, json={'access_token': 'a-token'}),
            profile=httpx.Response(200, json={'email': 'ada@test.com', 'given_name': 'Ada', 'family_name': 'L'}),
        )

        assert await self._exchange() == OAuthUserInfo(
            email='ada@test.com', first_name='Ada', last_name='L', provider='google'
        )


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
