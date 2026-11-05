"""An SSO sign-in comes back to the page that finishes it.

The OIDC callback validated the provider's token, created or found the
account, issued a one-time code — and sent the browser to
`/auth/callback`, a path the portal has no route for. Its catch-all sent
the browser to the dashboard with the code dropped, and the dashboard
sent it to sign in. Every SSO login succeeded at the provider and at the
server and signed nobody in, with nothing in either log to say so.

The portal finishes a provider sign-in on `/login`: `LoginPage` reads
`code` from the query, posts it to `/v1/auth/exchange` and goes on to
`returnTo`. Google's callback always went there. Found by the journey
(`tools/e2e_journey`), whose browser signs in through Dex.

The provider is not reached: `OidcClient.exchange` answers, because what
is under test is where the route sends the browser and that what it
carries is a code the portal can redeem.
"""

from __future__ import annotations

import pathlib
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from app.api.base_v1.schema.oidc import OidcSignInState
from app.config import settings
from app.services.auth.oauth_service import OAuthService
from app.services.auth.oidc import OidcClaims, OidcClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PORTAL_ROUTES = _ROOT / 'portal/src/App.tsx'
_LOGIN_PAGE = _ROOT / 'portal/src/auth/LoginPage.tsx'

pytestmark = pytest.mark.anyio


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, 'oidc_issuer', 'https://idp.acme.test')
    monkeypatch.setattr(settings, 'oidc_client_id', 'igni')
    monkeypatch.setattr(settings, 'oidc_client_secret', 'secret')
    monkeypatch.setattr(settings, 'oauth_frontend_base_url', 'https://portal.acme.test')

    async def _exchange(self, *, code, nonce, code_verifier):
        return OidcClaims(subject='s-1', email='sso.person@acme.test', email_verified=True, full_name='Sso Person')

    monkeypatch.setattr(OidcClient, 'exchange', _exchange)


async def _callback(client, return_to: str = ''):
    state = await OAuthService.generate_state(
        OidcSignInState(nonce='n-1', pkce='v' * 43, returnTo=return_to).as_metadata()
    )
    return await client.get(f'/auth/oidc/callback?code=x&state={state}', follow_redirects=False)


async def test_the_browser_goes_to_the_portals_sign_in_page(client, configured):
    resp = await _callback(client)

    assert resp.status_code == 302, resp.text
    where = urlsplit(resp.headers['location'])
    assert f'{where.scheme}://{where.netloc}' == 'https://portal.acme.test'
    assert where.path == '/login', f'the portal finishes a sign-in on /login, not {where.path}'


async def test_it_carries_a_code_the_portal_can_redeem_and_where_to_go_next(client, configured):
    resp = await _callback(client, return_to='/cli-auth?port=51234')

    query = parse_qs(urlsplit(resp.headers['location']).query)
    assert query['returnTo'] == ['/cli-auth?port=51234']
    redeemed = await client.post('/auth/exchange', json={'code': query['code'][0]})
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()['access_token']


def test_the_portal_has_that_page_and_it_reads_the_code():
    """The other half, so the two cannot drift apart again: a route at
    the path the callback names, whose page redeems `code`."""
    routes = _PORTAL_ROUTES.read_text()
    assert re.search(r'<Route\s+path="/login"\s+element=\{<LoginPage\s*/>\}', routes), 'no /login route'
    page = _LOGIN_PAGE.read_text()
    assert "searchParams.get('code')" in page
    assert "'/auth/exchange'" in page
