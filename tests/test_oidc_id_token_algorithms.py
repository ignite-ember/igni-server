"""What signing algorithms an ``id_token`` may use (F27).

``jwt.decode`` was called without ``algorithms=``, and python-jose reads
that as "any ``alg`` the token header claims"::

    if algorithms is not None and alg not in algorithms:
        raise JWSError('The specified alg value is not allowed')

Both classic attacks were tried against that and **both failed**:
``alg: none`` is rejected because jose cannot find an algorithm for a
JWK, and the RS256-to-HS256 confusion cannot even be constructed, since
jose refuses to use asymmetric key material as an HMAC secret. So this
file does not pin a fix for a live hole, and it would be wrong to file it
as one.

It exists because that protection was incidental — two guards inside a
dependency, not anything this code said. Naming the set makes the intent
explicit, and these tests keep it that way across a jose upgrade or a
move to another library, which is exactly when an incidental guard stops
holding.
"""

from __future__ import annotations

import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jws, jwt
from jose.backends import RSAKey
from jose.utils import base64url_encode
import pytest

from app.services.auth.oidc import ID_TOKEN_ALGORITHMS

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
    jwk = RSAKey(public_pem, 'RS256').to_dict()
    jwk['kid'] = 'k1'
    return {'private': private_pem, 'public': public_pem, 'jwks': {'keys': [jwk]}}


def _decode(token: str, jwks: dict):
    """The same call the OIDC service makes, algorithms included."""
    return jwt.decode(
        token,
        jwks,
        algorithms=ID_TOKEN_ALGORITHMS,
        audience=AUDIENCE,
        issuer=ISSUER,
        options={
            'verify_signature': True,
            'verify_aud': True,
            'verify_iss': True,
            'verify_exp': True,
        },
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
        """The classic confusion. jose will not even let it be built —
        asserted here so that if a future version does, this fails rather
        than the deployment."""
        try:
            forged = jws.sign(CLAIMS, issuer_keys['public'], algorithm='HS256', headers={'kid': 'k1'})
        except Exception:
            # Could not be constructed, which is the stronger outcome.
            return

        with pytest.raises(Exception):
            _decode(forged, issuer_keys['jwks'])

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
        """jose does not check ``nonce``, so it is the one guard that has
        to be explicit — worth pinning beside the algorithms, since both
        are things the library will not do for you."""
        import inspect

        from app.services.auth.oidc import OidcClient

        source = inspect.getsource(OidcClient.validate_id_token)

        assert "claims.get('nonce') != nonce" in source
