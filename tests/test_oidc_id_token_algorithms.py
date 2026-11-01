"""What signing algorithms an ``id_token`` may use (F27).

``jwt.decode`` was once called without ``algorithms=``, and python-jose
read that as "any ``alg`` the token header claims". Both classic attacks
were tried and both failed then, but only because of two guards inside
the library. python-jose has since been replaced by PyJWT behind
``app.security.jose`` (its PYSEC-2026-4298, a DER-encoded public key
accepted as an HMAC secret, had no fixed release).

So the allowlist is named here, ``app.security.jose`` refuses any
non-asymmetric algorithm whoever asks, and these tests forge the attacks
by hand rather than relying on a library refusing to build them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest

from app.security import jose
from app.services.auth.oidc import ID_TOKEN_ALGORITHMS


def base64url_encode(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b'=')


def _hs256(secret: bytes, claims: dict, kid: str = 'k1') -> str:
    """An HS256 token built by hand, so no library can refuse to make it."""
    header = base64url_encode(json.dumps({'alg': 'HS256', 'typ': 'JWT', 'kid': kid}).encode())
    payload = base64url_encode(json.dumps(claims).encode())
    signature = base64url_encode(hmac.new(secret, header + b'.' + payload, hashlib.sha256).digest())
    return (header + b'.' + payload + b'.' + signature).decode()


ISSUER = 'https://idp.example'
AUDIENCE = 'client-123'
CLAIMS = {
    'iss': ISSUER,
    'aud': AUDIENCE,
    'sub': 'attacker',
    'email': 'attacker@evil.test',
    'exp': 9999999999,
    'nonce': 'N',
}


@pytest.fixture(scope='module')
def issuer_keys():
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
    public_der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk['kid'] = 'k1'
    return {'private': private_pem, 'public': public_pem, 'der': public_der, 'jwks': {'keys': [jwk]}}


def _decode(token: str, jwks: dict):
    """The same call the OIDC service makes, algorithms included."""
    return jose.decode(
        token,
        jwks,
        algorithms=ID_TOKEN_ALGORITHMS,
        audience=AUDIENCE,
        issuer=ISSUER,
        require=('exp', 'iss', 'aud'),
    )


class TestTheAllowlist:
    def test_it_contains_no_hmac_algorithm(self):
        """The token is signed by the issuer with a key we hold only the
        public half of, so a shared-secret algorithm has no legitimate
        place — and it is the ingredient of the RS256-to-HS256 attack."""
        assert not [alg for alg in ID_TOKEN_ALGORITHMS if alg.startswith('HS')]

    def test_it_does_not_contain_none(self):
        assert 'none' not in [alg.lower() for alg in ID_TOKEN_ALGORITHMS]

    def test_it_contains_the_algorithm_real_providers_use(self):
        """RS256 is the OIDC default; an allowlist without it rejects
        every real login."""
        assert 'RS256' in ID_TOKEN_ALGORITHMS


class TestARealTokenStillWorks:
    def test_a_correctly_signed_token_decodes(self, issuer_keys):
        token = jwt.encode(CLAIMS, issuer_keys['private'], algorithm='RS256', headers={'kid': 'k1'})

        assert _decode(token, issuer_keys['jwks'])['sub'] == 'attacker'


class TestTheAttacksStayRefused:
    def test_alg_none_is_refused(self, issuer_keys):
        header = base64url_encode(json.dumps({'alg': 'none', 'typ': 'JWT'}).encode()).decode()
        payload = base64url_encode(json.dumps(CLAIMS).encode()).decode()

        with pytest.raises(Exception):
            _decode(f'{header}.{payload}.', issuer_keys['jwks'])

    def test_hs256_signed_with_the_public_key_is_refused(self, issuer_keys):
        """The classic confusion, built by hand with the PEM public key."""
        with pytest.raises(jose.TokenError):
            _decode(_hs256(issuer_keys['public'].encode(), CLAIMS), issuer_keys['jwks'])

    def test_hs256_signed_with_the_der_public_key_is_refused(self, issuer_keys):
        """PYSEC-2026-4298: the same confusion with the key DER-encoded,
        which is what python-jose's guard missed."""
        with pytest.raises(jose.TokenError):
            _decode(_hs256(issuer_keys['der'], CLAIMS), issuer_keys['jwks'])

    def test_no_caller_can_ask_for_an_hmac_algorithm(self, issuer_keys):
        """The guard is in `app.security.jose`, not in each caller's list."""
        forged = _hs256(issuer_keys['public'].encode(), CLAIMS)
        for algorithms in (['HS256'], ['RS256', 'HS256'], ['none']):
            with pytest.raises(ValueError):
                jose.decode(forged, issuer_keys['jwks'], algorithms=algorithms, audience=AUDIENCE)

    def test_a_token_signed_by_a_different_key_is_refused(self, issuer_keys):
        """The property underneath all of it: the signature has to be the
        issuer's."""
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_pem = other.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        token = jwt.encode(CLAIMS, other_pem, algorithm='RS256', headers={'kid': 'k1'})

        with pytest.raises(Exception):
            _decode(token, issuer_keys['jwks'])


class TestTheServiceUsesIt:
    def test_validate_id_token_passes_the_allowlist(self):
        """Asserted on the source: the value of the constant is that the
        call site uses it, and a test that only checks the constant would
        pass with the argument deleted."""
        import inspect

        from app.services.auth.oidc import OidcClient

        source = inspect.getsource(OidcClient.validate_id_token)

        assert 'algorithms=ID_TOKEN_ALGORITHMS' in source

    def test_the_nonce_is_still_checked_separately(self):
        """Nothing checks ``nonce`` for us, so it is the one guard that has
        to be explicit — worth pinning beside the algorithms, since both
        are things the library will not do for you."""
        import inspect

        from app.services.auth.oidc import OidcClient

        source = inspect.getsource(OidcClient.validate_id_token)

        assert "claims.get('nonce') != nonce" in source
