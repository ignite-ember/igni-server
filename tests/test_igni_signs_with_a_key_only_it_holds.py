"""igni signs its tokens with a key it generated, and publishes the public half.

What used to sign them was `JWT_SECRET_KEY`: a shared HMAC secret that an
operator had to mint, that every service validating a token had to hold,
and whose empty default once let anybody forge a session. Now:

* the server generates an RSA key on first start and stores it encrypted
  under `SECRET_ENCRYPTION_KEY` — a database dump alone cannot sign;
* anybody can check a token against `/.well-known/jwks.json`, and nobody
  but the server can mint one;
* a token says what it is (`token_use`) and who it is for (`aud`), and a
  check that does not match both refuses it;
* a rotation publishes the new key before it signs anything, so no
  replica or service sees a token it cannot check.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

from cryptography.fernet import Fernet
import jwt
import pytest
from sqlalchemy import create_engine, text

from app.config import settings
from app.db.sql.signing_keys import SigningKeyDB
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.identity.jwk import PublicJwk
from app.services.identity.key_ring import KeyRing, ProcessSigningKeys, SigningKeys
from app.services.secrets import decrypt_secret
from tests.conftest import TEST_DB_URL_SYNC

pytestmark = pytest.mark.anyio


@pytest.fixture
def encryption_key(monkeypatch):
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())
    monkeypatch.setattr(settings, 'secret_encryption_keys_previous', '')


def _rows() -> list[dict]:
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(text('SELECT * FROM signing_keys')).mappings().all()]
    finally:
        engine.dispose()


def _b64(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4))


class TestTheKeyIsTheServers:
    async def test_the_first_start_generates_one(self, encryption_key):
        await SigningKeyDB().ensure()

        rows = _rows()
        assert len(rows) == 1
        assert rows[0]['algorithm'] == 'RS256'

    async def test_it_is_stored_encrypted(self, encryption_key):
        """A dump of the table is not enough to sign."""
        await SigningKeyDB().ensure()

        stored = _rows()[0]['encrypted_private_key']
        assert 'PRIVATE KEY' not in stored
        assert 'PRIVATE KEY' in decrypt_secret(stored)

    async def test_starting_again_keeps_it(self, encryption_key):
        await SigningKeyDB().ensure()
        await SigningKeyDB().ensure()

        assert len(_rows()) == 1

    async def test_replicas_starting_together_make_one(self, encryption_key):
        """Two keys would mean each replica signs half the tokens with
        one the other may not have loaded yet."""
        await asyncio.gather(*(SigningKeyDB().ensure() for _ in range(5)))

        assert len(_rows()) == 1

    async def test_loading_signs_with_it(self, encryption_key):
        ring = await SigningKeys().load()

        assert ring.kid == _rows()[0]['kid']


class TestAnybodyCanCheckButOnlyIgniCanSign:
    async def test_the_published_key_checks_a_session(self, client):
        """What a service does with nothing but the server's URL."""
        token = JWTService().create_token('somebody', org_id='org-1')
        published = (await client.get('http://test/.well-known/jwks.json')).json()
        kid = jwt.get_unverified_header(token)['kid']
        key = next(k for k in published['keys'] if k['kid'] == kid)

        claims = jwt.decode(token, jwt.PyJWK(key), algorithms=['RS256'], audience=JWTService().issuer)

        assert claims['sub'] == 'somebody'
        assert claims['iss'] == JWTService().issuer
        assert claims['token_use'] == 'session'

    async def test_the_key_set_holds_no_private_member(self, client):
        published = (await client.get('http://test/.well-known/jwks.json')).json()

        for key in published['keys']:
            assert not {'d', 'p', 'q', 'dp', 'dq', 'qi'} & set(key)

    async def test_it_may_be_cached_but_not_for_long(self, client):
        response = await client.get('http://test/.well-known/jwks.json')

        assert response.headers['cache-control'] == 'public, max-age=300'

    async def test_the_metadata_names_every_endpoint(self, client):
        metadata = (await client.get('http://test/.well-known/openid-configuration')).json()
        issuer = JWTService().issuer

        assert metadata['issuer'] == issuer
        assert metadata['jwks_uri'] == f'{issuer}/.well-known/jwks.json'
        assert metadata['token_endpoint'] == f'{issuer}/v1/oauth/token'
        assert metadata['revocation_endpoint'] == f'{issuer}/v1/oauth/revoke'
        assert metadata['revocation_endpoint_auth_methods_supported'] == ['private_key_jwt']
        assert metadata['registration_endpoint'] == f'{issuer}/v1/clients'
        assert metadata['authorization_endpoint'].endswith('/authorize')
        assert metadata['code_challenge_methods_supported'] == ['S256']
        assert metadata['token_endpoint_auth_methods_supported'] == ['private_key_jwt']


class TestAForgeryIsRefused:
    def test_a_token_signed_with_another_key(self):
        stranger = KeyRing.generated()
        forged = JWTService(lambda: stranger).create_token('somebody')

        assert JWTService().decode_token(forged).user_id is None

    def test_hmac_keyed_with_the_public_key(self):
        """The classic RS256-to-HS256 confusion: sign with HMAC using the
        published key as the secret. Refused by the pinned algorithm list
        before any key is consulted."""
        public = ProcessSigningKeys().ring().published.keys[0]
        now = int(datetime.now(timezone.utc).timestamp())
        claims = {
            'sub': 'somebody',
            'aud': JWTService().issuer,
            'iss': JWTService().issuer,
            'token_use': 'session',
            'iat': now,
            'exp': now + 3600,
        }
        # Built by hand: PyJWT refuses to HMAC with a JWK, and the point is
        # that the *verifier* refuses it whatever made it.
        segments = [
            base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b'=')
            for part in ({'alg': 'HS256', 'typ': 'JWT', 'kid': public.kid}, claims)
        ]
        signing_input = b'.'.join(segments)
        mac = hmac.new(json.dumps(public.as_dict()).encode(), signing_input, hashlib.sha256).digest()
        forged = (signing_input + b'.' + base64.urlsafe_b64encode(mac).rstrip(b'=')).decode()

        assert JWTService().decode_token(forged).user_id is None

    def test_alg_none(self):
        header = base64.urlsafe_b64encode(
            json.dumps({'alg': 'none', 'kid': ProcessSigningKeys().ring().kid}).encode()
        ).rstrip(b'=')
        now = int(datetime.now(timezone.utc).timestamp())
        body = base64.urlsafe_b64encode(
            json.dumps(
                {
                    'sub': 'x',
                    'aud': JWTService().issuer,
                    'iss': JWTService().issuer,
                    'token_use': 'session',
                    'iat': now,
                    'exp': now + 3600,
                }
            ).encode()
        ).rstrip(b'=')

        assert JWTService().decode_token(f'{header.decode()}.{body.decode()}.').user_id is None


class TestATokenIsOnlyWhatItSaysItIs:
    def test_a_token_for_a_service_is_not_a_session(self):
        """A user token minted for CodeIndex, replayed against igni."""
        tokens = JWTService()
        for_codeindex = tokens.sign(
            subject='somebody', audience='codeindex-client', use=TokenUse.USER, lifetime=timedelta(minutes=10)
        )

        assert tokens.decode_token(for_codeindex).user_id is None

    def test_a_refresh_token_is_not_a_session(self):
        tokens = JWTService()
        refresh = tokens.sign(
            subject='somebody', audience=tokens.issuer, use=TokenUse.REFRESH, lifetime=timedelta(days=1)
        )

        assert tokens.decode_token(refresh).user_id is None

    def test_a_service_token_is_not_a_session(self):
        tokens = JWTService()
        service = tokens.sign(
            subject='a-client', audience=tokens.issuer, use=TokenUse.SERVICE, lifetime=timedelta(minutes=10)
        )

        assert tokens.decode_token(service).user_id is None

    async def test_the_portal_refuses_a_token_minted_for_a_service(self, client, db):
        from tests.conftest import create_user

        user = await create_user(db, email='a@example.com')
        tokens = JWTService()
        for_service = tokens.sign(
            subject=str(user.user_id), audience='some-client', use=TokenUse.USER, lifetime=timedelta(minutes=5)
        )

        response = await client.get('/portal/organizations', headers={'Authorization': f'Bearer {for_service}'})

        assert response.status_code == 401


class TestRotation:
    async def test_a_staged_key_is_published_before_it_signs(self, encryption_key):
        await SigningKeyDB().ensure()
        first = KeyRing.from_rows(await SigningKeyDB().all())
        staged = await SigningKeyDB().stage(activates_in=timedelta(minutes=10))

        ring = KeyRing.from_rows(await SigningKeyDB().all())

        assert ring.kid == first.kid
        assert ring.verifying_key(staged.kid) is not None

    async def test_it_signs_once_it_activates(self, encryption_key):
        await SigningKeyDB().ensure()
        staged = await SigningKeyDB().stage(activates_in=timedelta(minutes=10))

        later = KeyRing.from_rows(await SigningKeyDB().all(), now=datetime.now(timezone.utc) + timedelta(minutes=11))

        assert later.kid == staged.kid

    async def test_the_old_key_still_checks_what_it_signed(self, encryption_key):
        await SigningKeyDB().ensure()
        before = KeyRing.from_rows(await SigningKeyDB().all())
        token = JWTService(lambda: before).create_token('somebody')
        await SigningKeyDB().stage(activates_in=timedelta(seconds=0))

        after = KeyRing.from_rows(await SigningKeyDB().all())

        assert after.kid != before.kid
        assert JWTService(lambda: after).decode_token(token).user_id == 'somebody'

    async def test_a_key_outlived_by_every_token_is_pruned(self, encryption_key):
        await SigningKeyDB().ensure()
        old = _rows()[0]['kid']
        await SigningKeyDB().stage(activates_in=timedelta(seconds=0))

        assert await SigningKeyDB().prune(outlived_by=timedelta(days=31)) == []
        assert await SigningKeyDB().prune(outlived_by=timedelta(seconds=-1)) == [old]
        assert [r['kid'] for r in _rows()] != [old]


class TestTheThumbprint:
    def test_it_matches_rfc_7638(self):
        """The worked example in RFC 7638 §3.1. A super admin compares
        this value with what a service logged, so it must be the value
        every other implementation computes too."""
        key = PublicJwk.from_mapping(
            {
                'kty': 'RSA',
                'n': (
                    '0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECP'
                    'ebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY'
                    '368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0f'
                    'M4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw'
                ),
                'e': 'AQAB',
                'alg': 'RS256',
                'kid': '2011-04-29',
            }
        )

        assert key.thumbprint() == 'NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs'

    def test_a_private_key_is_refused_by_name(self):
        with pytest.raises(ValueError, match='private key'):
            PublicJwk.from_mapping({'kty': 'RSA', 'n': 'x', 'e': 'AQAB', 'd': 'secret'})
