"""OIDC sign-in.

Almost every test here is about **rejecting** a token, because that is
where the risk lives: a permissive ID-token check reads exactly like a
working one, and the failure is silent authentication bypass rather than
a broken login somebody reports.

The four properties that must hold — signature, issuer, audience, nonce —
each have a case that would pass if the check were missing.
"""

from datetime import datetime, timedelta, timezone
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from jwt.algorithms import RSAAlgorithm
from pydantic import ValidationError
import pytest

from app.api.base_v1.schema.oidc import OidcSignInState
from app.db.redis.oauth_state import OAuthStateDB
from app.services.auth.oidc import OidcClaims, OidcClient, OidcError, OidcNotConfigured

ISSUER = 'https://idp.acme.internal/realms/igni'
CLIENT_ID = 'igni-portal'
NONCE = 'nonce-for-this-login'


# An RSA keypair stands in for the IdP's signing key, and it has to be
# RSA rather than a symmetric one.
#
# This used to be HS256 with a shared secret, on the stated grounds that
# it "exercises that as faithfully as RSA while keeping the fixtures
# readable". The first half is no longer true, and the shortcut is what
# hid the question: ``validate_id_token`` now pins asymmetric algorithms
# only, and a test that signs id_tokens with a symmetric key cannot
# notice whether symmetric ones are accepted. It is also what a real
# issuer actually publishes — an HS256 id_token is verified with the
# client secret, not with anything from a JWKS, so this code could never
# have validated one.
def _keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


SIGNING_KEY, _PUBLIC_KEY = _keypair()
#: A second keypair, for "signed by somebody else" — its private half is
#: the wrong key, and its public half never reaches the JWKS.
OTHER_SIGNING_KEY, _ = _keypair()

_jwk = RSAAlgorithm.to_jwk(serialization.load_pem_public_key(_PUBLIC_KEY.encode()), as_dict=True)
_jwk['kid'] = 'test'
JWKS = {'keys': [_jwk]}


def _client(**overrides) -> OidcClient:
    return OidcClient(
        issuer=overrides.get('issuer', ISSUER),
        client_id=overrides.get('client_id', CLIENT_ID),
        client_secret=overrides.get('client_secret', 'secret'),
        redirect_uri='https://igni.acme.internal/v1/auth/oidc/callback',
    )


def _id_token(**overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        'iss': ISSUER,
        'aud': CLIENT_ID,
        'sub': 'user-1',
        'email': 'dev@acme.test',
        'email_verified': True,
        'name': 'Dev Eloper',
        'nonce': NONCE,
        'iat': now,
        'exp': now + timedelta(minutes=5),
        **overrides,
    }
    key = overrides.pop('_key', SIGNING_KEY)
    return jwt.encode(claims, key, algorithm='RS256')


@pytest.fixture
def offline_client(monkeypatch):
    """A client whose discovery and JWKS are already resolved.

    Validation is the subject; fetching is not, and a test that reaches
    the network is a test that fails for the wrong reasons.
    """
    client = _client()

    # `force` is accepted and ignored: the real `_jwks_keys` takes it so
    # an unknown `kid` can trigger a refetch during key rotation, and a
    # stub with the wrong signature fails with a TypeError about keyword
    # arguments rather than anything about OIDC.
    async def _jwks(self, *, force: bool = False):
        return JWKS

    monkeypatch.setattr(OidcClient, '_jwks_keys', _jwks)
    return client


class TestConfiguration:
    def test_all_three_settings_are_required(self):
        assert _client().is_configured is True
        assert _client(issuer='').is_configured is False
        assert _client(client_id='').is_configured is False
        assert _client(client_secret='').is_configured is False

    async def test_an_unconfigured_client_refuses_rather_than_guessing(self):
        with pytest.raises(OidcNotConfigured):
            await _client(issuer='').discover()

    def test_a_trailing_slash_on_the_issuer_is_normalised(self):
        """The issuer is compared against the token's ``iss`` claim, and
        a trailing slash would make every valid token fail."""
        assert _client(issuer=f'{ISSUER}/')._issuer == ISSUER


class TestIdTokenValidation:
    async def test_a_good_token_yields_the_identity(self, offline_client):
        claims = await offline_client.validate_id_token(_id_token(), nonce=NONCE)
        assert claims == OidcClaims(
            subject='user-1', email='dev@acme.test', email_verified=True, full_name='Dev Eloper'
        )

    async def test_a_token_signed_with_another_key_is_rejected(self, offline_client):
        """The whole attack. Everything else is defence in depth."""
        with pytest.raises(OidcError):
            await offline_client.validate_id_token(_id_token(_key=OTHER_SIGNING_KEY), nonce=NONCE)

    async def test_a_token_from_another_issuer_is_rejected(self, offline_client):
        """Otherwise any IdP willing to sign a token for anyone gets to
        mint our users."""
        with pytest.raises(OidcError):
            await offline_client.validate_id_token(_id_token(iss='https://evil.test'), nonce=NONCE)

    async def test_a_token_for_another_client_is_rejected(self, offline_client):
        """A token issued to a *different* client of the same IdP is a
        valid token — just not for us."""
        with pytest.raises(OidcError):
            await offline_client.validate_id_token(_id_token(aud='some-other-app'), nonce=NONCE)

    async def test_an_expired_token_is_rejected(self, offline_client):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        with pytest.raises(OidcError):
            await offline_client.validate_id_token(_id_token(exp=past), nonce=NONCE)

    async def test_a_replayed_nonce_is_rejected(self, offline_client):
        """Nothing checks nonce for us; this is our own guard, and without
        it a token obtained from the same IdP elsewhere could be replayed
        into our callback."""
        with pytest.raises(OidcError, match='nonce'):
            await offline_client.validate_id_token(_id_token(nonce='someone-elses'), nonce=NONCE)

    async def test_a_token_with_no_nonce_is_rejected(self, offline_client):
        token = jwt.encode(
            {
                'iss': ISSUER,
                'aud': CLIENT_ID,
                'sub': 'user-1',
                'email': 'dev@acme.test',
                'exp': datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            SIGNING_KEY,
            algorithm='RS256',
        )
        with pytest.raises(OidcError, match='nonce'):
            await offline_client.validate_id_token(token, nonce=NONCE)


class TestClaimMapping:
    async def test_a_missing_subject_is_rejected(self, offline_client):
        """Omitted rather than null: PyJWT rejects a null ``sub`` itself,
        before our own check runs, so an absent claim is the only way to
        reach it."""
        token = jwt.encode(
            {
                'iss': ISSUER,
                'aud': CLIENT_ID,
                'email': 'dev@acme.test',
                'nonce': NONCE,
                'exp': datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            SIGNING_KEY,
            algorithm='RS256',
        )
        with pytest.raises(OidcError, match='sub'):
            await offline_client.validate_id_token(token, nonce=NONCE)

    async def test_a_missing_email_names_the_setting_to_change(self, offline_client):
        """Providers disagree about which claim holds the address, so the
        error has to say how to fix it rather than just failing."""
        with pytest.raises(OidcError, match='oidc_email_claim'):
            await offline_client.validate_id_token(_id_token(email=None), nonce=NONCE)

    async def test_an_absent_verified_flag_means_unverified(self, offline_client):
        """Absent means unknown. Treating it as verified would let an IdP
        that does not verify addresses take over accounts by email
        match."""
        claims = await offline_client.validate_id_token(_id_token(email_verified=None), nonce=NONCE)
        assert claims.email_verified is False

    async def test_a_missing_name_is_allowed(self, offline_client):
        claims = await offline_client.validate_id_token(_id_token(name=None), nonce=NONCE)
        assert claims.full_name is None

    async def test_the_email_claim_is_configurable(self, offline_client, monkeypatch):
        """Entra puts it in ``upn`` rather than ``email``."""
        from app.config import settings

        monkeypatch.setattr(settings, 'oidc_email_claim', 'upn')
        claims = await offline_client.validate_id_token(_id_token(email=None, upn='dev@acme.test'), nonce=NONCE)
        assert claims.email == 'dev@acme.test'


class TestDiscovery:
    async def test_a_document_declaring_a_different_issuer_is_rejected(self, monkeypatch):
        """Either a misconfiguration or a redirect somewhere we did not
        intend to trust — and both should stop the login."""
        import httpx

        async def _get(self, url, *a, **k):
            return httpx.Response(
                200,
                json={'issuer': 'https://evil.test', 'jwks_uri': 'https://evil.test/jwks'},
                request=httpx.Request('GET', url),
            )

        monkeypatch.setattr(httpx.AsyncClient, 'get', _get)
        with pytest.raises(OidcError, match='issuer'):
            await _client().discover()

    async def test_a_matching_issuer_is_accepted(self, monkeypatch):
        import httpx

        async def _get(self, url, *a, **k):
            return httpx.Response(
                200,
                json={
                    'issuer': ISSUER,
                    'jwks_uri': f'{ISSUER}/jwks',
                    'authorization_endpoint': f'{ISSUER}/auth',
                    'token_endpoint': f'{ISSUER}/token',
                },
                request=httpx.Request('GET', url),
            )

        monkeypatch.setattr(httpx.AsyncClient, 'get', _get)
        document = await _client().discover()
        assert document['token_endpoint'] == f'{ISSUER}/token'

    async def test_the_authorize_url_carries_state_and_nonce(self, monkeypatch):
        """Both have to reach the IdP or the round trip cannot be
        validated on the way back."""
        import httpx

        async def _get(self, url, *a, **k):
            return httpx.Response(
                200,
                json={
                    'issuer': ISSUER,
                    'jwks_uri': f'{ISSUER}/jwks',
                    'authorization_endpoint': f'{ISSUER}/auth',
                },
                request=httpx.Request('GET', url),
            )

        monkeypatch.setattr(httpx.AsyncClient, 'get', _get)
        url = await _client().authorize_url(state='state-1', nonce='nonce-1')
        assert 'state=state-1' in url
        assert 'nonce=nonce-1' in url
        assert 'response_type=code' in url


class TestNonceGeneration:
    def test_nonces_are_unique(self):
        assert OidcClient.generate_nonce() != OidcClient.generate_nonce()

    def test_a_nonce_has_enough_entropy_to_be_unguessable(self):
        assert len(OidcClient.generate_nonce()) >= 24


class TestEndpointsWhenUnconfigured:
    async def test_the_redirect_404s_rather_than_erroring(self, client):
        """A deployment using local accounts and GitHub is valid, so
        "OIDC is off" is a 404 on the route and not a 500."""
        resp = await client.get('/auth/oidc', follow_redirects=False)
        assert resp.status_code == 404

    async def test_the_callback_404s_too(self, client):
        resp = await client.get('/auth/oidc/callback?code=x&state=y', follow_redirects=False)
        assert resp.status_code == 404


class TestTheStateThatSurvivesTheRoundTrip:
    """What the redirect hands its own callback, minutes and one
    identity provider later.

    It was a dict literal going out and three `.get(key, '')` reads
    coming back, with the two presence checks the security of the flow
    depends on written out by hand beside one of the readers. Those
    checks are now the model's, which is the only reason this class can
    assert them without going through an endpoint.
    """

    async def test_a_state_round_trips_through_the_store(self):
        """The record goes into Redis sealed and comes back as the model."""
        original = OidcSignInState(nonce='n-1', pkce='v-1', returnTo='/docs')
        state = secrets.token_urlsafe(32)

        await OAuthStateDB().store(state, original)
        restored = await OAuthStateDB().claim(state, OidcSignInState)

        assert restored == original

    async def test_an_absent_return_to_is_not_stored_as_an_empty_string(self, monkeypatch):
        """The redirect used to build the key conditionally; that has to
        keep being true or the callback appends `?returnTo=` to the
        frontend URL."""
        sealed: list[str] = []
        monkeypatch.setattr('app.db.redis.oauth_state.seal', lambda plaintext: sealed.append(plaintext) or plaintext)
        monkeypatch.setattr('app.db.redis.oauth_state.unseal', lambda stored: stored)
        state = secrets.token_urlsafe(32)

        await OAuthStateDB().store(state, OidcSignInState(nonce='n-1', pkce='v-1'))

        assert 'returnTo' not in sealed[0]
        assert (await OAuthStateDB().claim(state, OidcSignInState)).return_to == ''

    @pytest.mark.parametrize(
        'metadata',
        [
            pytest.param({'pkce': 'v-1'}, id='no-nonce'),
            pytest.param({'nonce': 'n-1'}, id='no-pkce'),
            pytest.param({'nonce': '', 'pkce': 'v-1'}, id='empty-nonce'),
            pytest.param({'nonce': 'n-1', 'pkce': ''}, id='empty-pkce'),
            pytest.param({'link_user_id': 'u-1'}, id='another-flows-state'),
            pytest.param({}, id='nothing-at-all'),
        ],
    )
    def test_state_without_both_secrets_is_refused(self, metadata):
        """Empty is the dangerous value, not merely an invalid one.

        A code injected into the callback arrives with somebody else's
        state. If a missing verifier were allowed through, the exchange
        would omit `code_verifier` and the provider — which cannot know
        we meant to use PKCE — would honour it.

        `another-flows-state` is the realistic case: the same Redis
        store backs the GitHub and GitLab link flows, whose records
        carry entirely different keys.
        """
        with pytest.raises(ValidationError):
            OidcSignInState.model_validate(metadata)
