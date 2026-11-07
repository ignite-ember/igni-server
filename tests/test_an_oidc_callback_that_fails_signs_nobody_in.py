"""The OIDC callback's refusals, through the route.

`tests/test_oidc.py` covers the client — token validation, discovery,
claim mapping — and the callback only when OIDC is off (404). The three
ways a *configured* callback refuses had no test: state that is not ours,
a token the client rejects, and an identity with no verified address.
Each of them, broken, signs somebody in who should not be.

The provider is never reached: `OidcClient.exchange` is replaced, because
what is under test is what the route does with its answer.
"""

from __future__ import annotations

import pytest

from app.api.base_v1.schema.oidc import OidcSignInState
from app.config import settings
from app.services.auth.oauth_service import OAuthService
from app.services.auth.oidc import OidcClaims, OidcClient, OidcError


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, 'oidc_issuer', 'https://idp.acme.test')
    monkeypatch.setattr(settings, 'oidc_client_id', 'igni')
    monkeypatch.setattr(settings, 'oidc_client_secret', 'secret')
    monkeypatch.setattr(settings, 'oidc_link_by_email', False)


async def _state() -> str:
    return await OAuthService.generate_state(OidcSignInState(nonce='n-1', pkce='v' * 43))


def _exchange_answers(monkeypatch, outcome):
    async def _exchange(self, *, code, nonce, code_verifier):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(OidcClient, 'exchange', _exchange)


async def test_a_state_we_never_issued_is_refused(client, configured):
    resp = await client.get('/auth/oidc/callback?code=x&state=never-issued', follow_redirects=False)
    assert resp.status_code == 400


async def test_a_token_the_client_rejects_is_a_401_that_says_nothing_more(client, configured, monkeypatch):
    _exchange_answers(monkeypatch, OidcError('nonce replayed'))
    resp = await client.get(f'/auth/oidc/callback?code=x&state={await _state()}', follow_redirects=False)
    assert resp.status_code == 401
    assert 'nonce' not in resp.text


async def test_an_unverified_address_is_refused_when_linking_by_email_is_off(client, configured, monkeypatch):
    _exchange_answers(monkeypatch, OidcClaims(subject='s-1', email='someone@acme.test', email_verified=False))
    resp = await client.get(f'/auth/oidc/callback?code=x&state={await _state()}', follow_redirects=False)
    assert resp.status_code == 403
    assert 'location' not in resp.headers


async def test_a_state_is_good_for_one_callback_only(client, configured, monkeypatch):
    _exchange_answers(monkeypatch, OidcError('whatever'))
    state = await _state()
    first = await client.get(f'/auth/oidc/callback?code=x&state={state}', follow_redirects=False)
    second = await client.get(f'/auth/oidc/callback?code=x&state={state}', follow_redirects=False)
    assert (first.status_code, second.status_code) == (401, 400)
