"""OIDC, closing AC-9 — and the row that outlived the build.

AC-9 was recorded as a `Gap` reading "the largest genuinely new build".
It was not: `app/services/auth/oidc.py` and its 31 tests already existed
when I came to do it, discovery through nonce validation, with the
security-critical checks argued in the module docstring. The gap was in
the tracker, the same shape as F97's setting that outlived its
subsystem — and the honest response is to audit the thing rather than
either trust the row or trust the code.

What the audit found, and what this file tests:

* **no PKCE.** Not required for a confidential client by RFC 7636 and
  required for every client by RFC 9700, because the attack it stops —
  authorization code injection — is not about client secrecy;
* **JWKS cached for an hour with no way to miss.** A provider rotating
  its signing key refused every login until the cache expired, reporting
  "signature verification failed", which sends an operator looking for
  a wrong secret;
* **`client_secret_post` hardcoded.** The spec's default is
  `client_secret_basic`, and a provider registered for basic only
  answers 401 in a way that reads like a wrong secret;
* **a plaintext issuer accepted.** The discovery document and the
  signing keys arrive over that connection;
* **no button.** The endpoints existed and nothing in the portal linked
  to them, so for the only provider an air-gapped deployment can reach,
  the feature was unreachable by design.
"""

from __future__ import annotations

import base64
import hashlib
import pathlib

from cryptography.fernet import Fernet
import pytest

from app.config import settings
from app.security.startup_checks import check_deployment
from app.services.auth.oauth_service import OAuthService
from app.services.auth.oidc import OidcClient, OidcError

_ROOT = pathlib.Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.anyio


def _client(**overrides) -> OidcClient:
    return OidcClient(
        **{
            'issuer': 'https://idp.example.com',
            'client_id': 'igni',
            'client_secret': 'shh',
            'redirect_uri': 'https://igni.example.com/v1/auth/oidc/callback',
            **overrides,
        }
    )


class TestPkce:
    def test_a_verifier_is_long_enough_to_be_unguessable(self):
        """RFC 7636 asks for 43-128 characters of entropy."""
        verifier = OidcClient.generate_pkce_verifier()

        assert 43 <= len(verifier) <= 128, len(verifier)

    def test_verifiers_are_not_reused(self):
        assert len({OidcClient.generate_pkce_verifier() for _ in range(50)}) == 50

    def test_the_challenge_is_the_s256_of_the_verifier(self):
        """S256, never `plain`. `plain` sends the verifier itself to the
        authorization endpoint, which puts it in the same logs and
        referrers the code ends up in — so it defends against nothing."""
        verifier = OidcClient.generate_pkce_verifier()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')

        assert OidcClient.pkce_challenge(verifier) == expected

    def test_the_challenge_carries_no_padding(self):
        """base64url without `=`, per RFC 7636. Padding is rejected by
        some providers and silently mangled in a query string by others."""
        for _ in range(20):
            assert '=' not in OidcClient.pkce_challenge(OidcClient.generate_pkce_verifier())

    async def test_the_authorize_url_carries_the_challenge_and_the_method(self, monkeypatch):
        async def _discovery(self):
            return {'issuer': 'https://idp.example.com', 'authorization_endpoint': 'https://idp.example.com/auth'}

        monkeypatch.setattr(OidcClient, 'discover', _discovery)

        url = await _client().authorize_url(state='s', nonce='n', code_challenge='abc123')

        assert 'code_challenge=abc123' in url
        assert 'code_challenge_method=S256' in url

    async def test_it_is_omitted_when_there_is_no_challenge(self, monkeypatch):
        """So the parameter cannot appear empty, which some providers
        reject and others treat as `plain`."""

        async def _discovery(self):
            return {'issuer': 'https://idp.example.com', 'authorization_endpoint': 'https://idp.example.com/auth'}

        monkeypatch.setattr(OidcClient, 'discover', _discovery)

        url = await _client().authorize_url(state='s', nonce='n')

        assert 'code_challenge' not in url

    async def test_the_endpoint_stores_the_verifier_server_side(self, client, monkeypatch):
        """It must never reach the browser — only its challenge does.

        This pair used to assert on the *source text* of the endpoint,
        looking for the literal `'pkce': verifier` and
        `if not metadata.get('pkce')`. That is the "list wearing a
        rule's docstring" shape this repo has been caught by twice
        before (see `TestNoHandlerLogsARawPayload`): the strings went
        stale the moment the state record became a model, while the
        control they stand for was still in place. Worse, the reverse
        could happen — the literals could survive a change that broke
        the control, and these would still pass.

        Driven through the real redirect instead. What matters is
        observable: the verifier is in what gets stored, it is *not* in
        what the browser is sent, and the challenge that is sent is the
        S256 of it.
        """
        stored: dict[str, dict[str, str]] = {}

        async def _capture(record):
            stored['record'] = record
            return 'state-token'

        async def _discovery(self):
            return {'issuer': 'https://idp.example.com', 'authorization_endpoint': 'https://idp.example.com/auth'}

        monkeypatch.setattr(settings, 'oidc_issuer', 'https://idp.example.com', raising=False)
        monkeypatch.setattr(settings, 'oidc_client_id', 'igni', raising=False)
        monkeypatch.setattr(settings, 'oidc_client_secret', 'shh', raising=False)
        monkeypatch.setattr(OidcClient, 'discover', _discovery)
        monkeypatch.setattr(OAuthService, 'generate_state', _capture)

        response = await client.get('/auth/oidc', follow_redirects=False)

        assert response.status_code == 302, response.text
        verifier = stored['record'].pkce
        assert verifier, 'no verifier was stored'

        location = response.headers['location']
        assert verifier not in location, 'the verifier itself reached the browser'
        assert f'code_challenge={OidcClient.pkce_challenge(verifier)}' in location
        assert 'code_challenge_method=S256' in location

    async def test_a_callback_without_a_stored_verifier_is_refused(self, client, monkeypatch):
        """An optional defence is not one. If a missing verifier were
        allowed through, the exchange would omit `code_verifier` and the
        provider — which cannot know we meant to use PKCE — would honour
        it, so an injected code would work exactly as before.

        The realistic way to reach this is not a hand-edited record: the
        same Redis store backs the GitHub and GitLab link flows, whose
        state carries entirely different keys.
        """
        exchanged: list[str] = []

        async def _state(state: str, shape):
            return shape.model_validate({'nonce': 'n-1'})  # a nonce, no verifier

        async def _exchange(self, **kwargs):
            exchanged.append(kwargs.get('code', ''))
            raise AssertionError('the code was exchanged without a verifier')

        monkeypatch.setattr(settings, 'oidc_issuer', 'https://idp.example.com', raising=False)
        monkeypatch.setattr(settings, 'oidc_client_id', 'igni', raising=False)
        monkeypatch.setattr(settings, 'oidc_client_secret', 'shh', raising=False)
        monkeypatch.setattr(OAuthService, 'validate_state', _state)
        monkeypatch.setattr(OidcClient, 'exchange', _exchange)

        response = await client.get('/auth/oidc/callback?code=injected&state=s', follow_redirects=False)

        assert response.status_code == 400, response.text
        assert not exchanged, 'the exchange ran anyway'


class TestKeyRotation:
    def test_an_unknown_kid_asks_for_the_keys_again(self):
        """Otherwise a provider that rotates its signing key refuses every
        login for up to an hour, reporting a signature failure."""
        import jwt as jose_jwt

        token = jose_jwt.encode({'sub': 'x'}, 'secret', algorithm='HS256', headers={'kid': 'new-key'})

        assert OidcClient._kid_is_unknown(token, {'keys': [{'kid': 'old-key'}]}) is True

    def test_a_known_kid_does_not(self):
        """The refetch has to stay rare: it puts the provider on the
        critical path of a login."""
        import jwt as jose_jwt

        token = jose_jwt.encode({'sub': 'x'}, 'secret', algorithm='HS256', headers={'kid': 'old-key'})

        assert OidcClient._kid_is_unknown(token, {'keys': [{'kid': 'old-key'}]}) is False

    def test_a_token_with_no_kid_does_not(self):
        """Conservative by construction: this decides whether to refetch,
        and a path that can be pushed into refetching on every request is
        a way to hammer somebody's IdP."""
        import jwt as jose_jwt

        token = jose_jwt.encode({'sub': 'x'}, 'secret', algorithm='HS256')

        assert OidcClient._kid_is_unknown(token, {'keys': [{'kid': 'old-key'}]}) is False

    def test_an_unreadable_token_does_not(self):
        assert OidcClient._kid_is_unknown('not-a-jwt', {'keys': [{'kid': 'k'}]}) is False

    def test_an_empty_key_set_does_not(self):
        """Nothing is known, so nothing can be said to be unknown — and
        the normal validation will fail informatively anyway."""
        import jwt as jose_jwt

        token = jose_jwt.encode({'sub': 'x'}, 'secret', algorithm='HS256', headers={'kid': 'k'})

        assert OidcClient._kid_is_unknown(token, {'keys': []}) is False


class TestClientAuthentication:
    async def test_basic_is_used_when_that_is_what_the_provider_supports(self, monkeypatch):
        """`client_secret_post` was hardcoded. The spec's default is
        basic, and a provider registered for basic only answers 401 with
        a message about the client — which reads like a wrong secret
        rather than a wrong method."""
        captured: dict = {}

        async def _discovery(self):
            return {
                'issuer': 'https://idp.example.com',
                'token_endpoint': 'https://idp.example.com/token',
                'token_endpoint_auth_methods_supported': ['client_secret_basic'],
            }

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, url, data=None, auth=None, headers=None):
                captured['data'] = data
                captured['auth'] = auth
                return _Response()

        monkeypatch.setattr(OidcClient, 'discover', _discovery)
        monkeypatch.setattr('app.services.auth.oidc.httpx.AsyncClient', lambda **_: _Client())

        with pytest.raises(OidcError, match='no id_token'):
            await _client().exchange(code='c', nonce='n', code_verifier='v')

        assert captured['auth'] == ('igni', 'shh'), captured['auth']
        assert 'client_secret' not in captured['data'], 'the secret went in the body as well as the header'
        assert captured['data']['code_verifier'] == 'v'

    async def test_post_is_used_when_the_provider_prefers_it(self, monkeypatch):
        captured: dict = {}

        async def _discovery(self):
            return {
                'issuer': 'https://idp.example.com',
                'token_endpoint': 'https://idp.example.com/token',
                'token_endpoint_auth_methods_supported': ['client_secret_post', 'client_secret_basic'],
            }

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, url, data=None, auth=None, headers=None):
                captured['data'] = data
                captured['auth'] = auth
                return _Response()

        monkeypatch.setattr(OidcClient, 'discover', _discovery)
        monkeypatch.setattr('app.services.auth.oidc.httpx.AsyncClient', lambda **_: _Client())

        with pytest.raises(OidcError, match='no id_token'):
            await _client().exchange(code='c', nonce='n')

        assert captured['data']['client_secret'] == 'shh'

    async def test_a_provider_we_cannot_authenticate_to_says_so(self, monkeypatch):
        """`private_key_jwt` and `tls_client_auth` are real and not
        implemented. Saying which is supported beats a 401 the operator
        has to guess at."""

        async def _discovery(self):
            return {
                'issuer': 'https://idp.example.com',
                'token_endpoint': 'https://idp.example.com/token',
                'token_endpoint_auth_methods_supported': ['private_key_jwt'],
            }

        monkeypatch.setattr(OidcClient, 'discover', _discovery)

        with pytest.raises(OidcError, match='private_key_jwt'):
            await _client().exchange(code='c', nonce='n')


class TestTheIssuerMustBeHttps:
    """Everything OIDC guarantees rests on the discovery document and the
    JWKS being genuine, and over plaintext neither is."""

    def test_a_plaintext_issuer_is_refused(self):
        with pytest.raises(OidcError, match='not accepted'):
            _client(issuer='http://idp.example.com')._require_configured()

    def test_localhost_is_allowed(self):
        """That is how anybody tests against a local Keycloak, and the
        traffic never leaves the machine."""
        for issuer in ('http://localhost:8080/realms/igni', 'http://127.0.0.1:8080'):
            _client(issuer=issuer)._require_configured()

    def test_a_host_that_merely_starts_with_localhost_is_not(self):
        """`localhost.evil.example.com` is somebody else's machine."""
        with pytest.raises(OidcError):
            _client(issuer='http://localhost.evil.example.com')._require_configured()

    def test_https_is_allowed(self):
        _client(issuer='https://idp.example.com')._require_configured()

    def test_the_startup_check_refuses_it_too(self, monkeypatch):
        """Better found at boot than at the first sign-in attempt."""
        monkeypatch.setattr(settings, 'app_env', 'prod')
        monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())
        monkeypatch.setattr(settings, 'oidc_issuer', 'http://idp.example.com')
        monkeypatch.setattr(settings, 'oidc_client_id', 'igni')
        monkeypatch.setattr(settings, 'oidc_client_secret', 'shh')

        problems = check_deployment(raise_on_unsafe=False)

        assert any('not HTTPS' in problem for problem in problems), problems


class TestHalfConfiguredOidcIsNotSilent:
    def test_two_of_three_settings_warns(self, monkeypatch):
        """`/v1/auth/oidc` answers 404 unless all three are set, which is
        right and silent: an operator who set two sees an endpoint that
        does not exist and concludes the feature is missing."""
        monkeypatch.setattr(settings, 'app_env', 'prod')
        monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())
        monkeypatch.setattr(settings, 'oidc_issuer', 'https://idp.example.com')
        monkeypatch.setattr(settings, 'oidc_client_id', 'igni')
        monkeypatch.setattr(settings, 'oidc_client_secret', '')

        problems = check_deployment(raise_on_unsafe=False)

        assert any('partly configured' in problem for problem in problems), problems
        assert any('OIDC_CLIENT_SECRET' in problem for problem in problems), problems

    def test_none_of_them_is_silent(self, monkeypatch):
        """A deployment using local accounts and GitHub is perfectly
        valid and must not be nagged."""
        monkeypatch.setattr(settings, 'app_env', 'prod')
        monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())
        for name in ('oidc_issuer', 'oidc_client_id', 'oidc_client_secret'):
            monkeypatch.setattr(settings, name, '')

        problems = check_deployment(raise_on_unsafe=False)

        assert not any('OIDC' in problem for problem in problems), problems


class TestTheSignInPageKnowsWhatExists:
    async def test_it_reports_the_enabled_providers(self, client, monkeypatch):
        """Listing a provider is not enough; it has to be able to work.

        The credentials are set here rather than assumed: a test that
        relied on whatever the machine's untracked `.env` configures is
        green locally and red in CI, and no local run could show it.
        """
        monkeypatch.setattr(settings, 'oauth_providers', ['google'])
        monkeypatch.setattr(settings, 'oauth_google_client_id', 'test-client-id')
        monkeypatch.setattr(settings, 'oauth_google_client_secret', 'test-client-secret')

        body = (await client.get('/auth/methods')).json()

        assert body['oauth_providers'] == ['google']
        assert body['password'] is True

    async def test_it_reports_sso_when_configured(self, client, monkeypatch):
        monkeypatch.setattr(settings, 'oidc_issuer', 'https://idp.example.com')
        monkeypatch.setattr(settings, 'oidc_client_id', 'igni')
        monkeypatch.setattr(settings, 'oidc_client_secret', 'shh')
        monkeypatch.setattr(settings, 'oidc_display_name', 'Acme Okta')

        body = (await client.get('/auth/methods')).json()

        assert body['sso'] == {'enabled': True, 'label': 'Acme Okta', 'start_url': '/v1/auth/oidc'}

    async def test_it_reports_sso_as_absent_when_not(self, client, monkeypatch):
        monkeypatch.setattr(settings, 'oidc_issuer', '')

        assert (await client.get('/auth/methods')).json()['sso']['enabled'] is False

    async def test_it_needs_no_credentials(self, client):
        """It is what the sign-in page renders from, so it has to be
        readable before anybody is signed in."""
        response = await client.get('/auth/methods')

        assert response.status_code == 200, response.status_code

    async def test_it_leaks_no_configuration(self, client, monkeypatch):
        """Only which methods exist and what to call them. A corporate
        IdP hostname is a small thing to hand an anonymous caller and
        there is no reason to."""
        monkeypatch.setattr(settings, 'oidc_issuer', 'https://sso.internal.acme.example')
        monkeypatch.setattr(settings, 'oidc_client_id', 'igni-client-id')
        monkeypatch.setattr(settings, 'oidc_client_secret', 'the-secret')

        body = (await client.get('/auth/methods')).text

        assert 'sso.internal.acme.example' not in body
        assert 'igni-client-id' not in body
        assert 'the-secret' not in body

    def test_the_portal_renders_from_it_rather_than_hardcoding(self):
        """The page used to show three providers whether or not the
        server accepted them, and the sign-up page a Google button on every
        deployment (B39). Both render the server's answer, through one
        component and one query."""
        auth = _ROOT / 'portal/src/auth'

        assert "'/auth/methods'" in (auth / 'useAuthMethods.ts').read_text()
        for page in ('LoginPage.tsx', 'SignUpPage.tsx'):
            source = (auth / page).read_text()
            assert 'useAuthMethods()' in source, page
            assert '<OtherWaysIn' in source, page
            # The old shape: a literal button per provider.
            assert "handleOAuth('" not in source, f'{page}: a hardcoded provider button is back'
