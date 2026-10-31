"""Tests for OAuth flows: redirect, callback, and find_or_create_user.

These are about the mechanics of a provider round trip, not about
which providers a deployment chooses to offer, so each test gives
Google credentials, which is what a deployment offering it would do.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.sql.models.org_membership import OrgMembership
from app.services.auth.oauth_service import OAuthService, OAuthUserInfo
from tests.conftest import add_invited_member, create_org, create_user


@pytest.fixture(autouse=True)
def _google_sign_in_enabled(monkeypatch):
    """A deployment that asked for Google sign-in and configured it."""
    monkeypatch.setattr(settings, 'oauth_providers', ['google'])
    monkeypatch.setattr(settings, 'oauth_google_client_id', 'google-client-id')
    monkeypatch.setattr(settings, 'oauth_google_client_secret', 'google-client-secret')


class TestOAuthRedirect:
    async def test_google_redirect(self, client, monkeypatch):
        async def fake_generate_state(metadata=None):
            return 'fake-state-token'

        monkeypatch.setattr(OAuthService, 'generate_state', staticmethod(fake_generate_state))

        resp = await client.get('/auth/google', follow_redirects=False)

        assert resp.status_code == 302
        location = resp.headers['location']
        assert 'accounts.google.com' in location
        assert 'fake-state-token' in location


class TestOAuthCallback:
    async def test_google_callback_new_user(self, client, monkeypatch):
        async def fake_validate_state(state):
            return {}

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

        async def fake_validate_state(state):
            return {}

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


class TestFindOrCreateUser:
    async def test_creates_new_user(self, db, monkeypatch):
        user_info = OAuthUserInfo(
            email='brand-new@test.com',
            first_name='Brand',
            last_name='New',
            provider='google',
        )

        result = await OAuthService.find_or_create_user_and_get_token(user_info)

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

        result = await OAuthService.find_or_create_user_and_get_token(user_info)

        assert result.is_new_user is False
        assert result.email == 'existing-oauth@test.com'
        assert result.token.access_token
