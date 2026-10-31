"""Nothing Redis holds is a usable secret. F151, DP-1.

`TestEveryCredentialColumnIsEncrypted` holds the databases to this. Redis
is the other store, and it held, for minutes at a time: six-digit sign-in
codes, the codes that hand a token to the browser, the token itself, OIDC
state with its PKCE verifier, OAuth authorization codes, and a
self-registered GPU node's API key. A Redis snapshot, replica or
`MONITOR` session carried all of it.

So each flow is driven against the real Redis the suite uses, and then
**every key and every value in it** — strings, hashes, sets, sorted sets
and lists — is searched for the plaintext. Searching everything, rather
than the keys each store is known to write, is what catches a second
copy written somewhere nobody thought to look.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid

from cryptography.fernet import Fernet
import pytest
import redis.asyncio as aioredis

from app.config import settings
from app.db.redis.auth_handoff import AuthHandoffDB
from app.db.redis.authorization_codes import AuthorizationCodeDB, PendingAuthorization
from app.db.redis.oauth_state import OAuthStateDB
from app.services.ai.node_registry import NodeRegistry
from app.services.ai.schemas import InferenceBackend
from app.services.auth.otp_service import OtpPurpose, OTPService
from tests.conftest import create_user

pytestmark = pytest.mark.anyio

NODE_TOKEN = 'node-token-for-the-redis-scan'


async def _everything_in_redis() -> list[str]:
    """Every key and every value in the suite's Redis database, as text."""
    client: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[assignment]
    found: list[str] = []
    try:
        async for key in client.scan_iter(match='*', count=1000):
            found.append(key)
            kind = await client.type(key)
            if kind == 'string':
                found.append(await client.get(key) or '')
            elif kind == 'hash':
                for field, value in (await client.hgetall(key)).items():  # type: ignore[misc]
                    found += [field, value]
            elif kind == 'set':
                found += list(await client.smembers(key))  # type: ignore[misc]
            elif kind == 'zset':
                found += [member for member, _ in await client.zrange(key, 0, -1, withscores=True)]
            elif kind == 'list':
                found += list(await client.lrange(key, 0, -1))  # type: ignore[misc]
    finally:
        await client.aclose()
    return found


async def _assert_nowhere(*secrets_: str) -> None:
    stored = await _everything_in_redis()
    assert stored, 'Redis is empty, so this proves nothing'
    for secret in secrets_:
        holders = [entry for entry in stored if secret in entry]
        assert not holders, f'{secret!r} is in Redis in the clear: {holders[:3]}'


class TestTheFlowsLeaveNothingUsable:
    async def test_a_sign_in_code(self, client, db):
        """Six digits: the one that most needs it, because a million
        candidates is no protection once something narrows them."""
        email = f'scan-{uuid.uuid4().hex[:8]}@example.com'
        await create_user(db, email=email)
        await db.commit()

        issued = await OTPService().generate_signin_code(email)

        await _assert_nowhere(issued.code)

        # And it still signs in, once.
        response = await client.post('/sign-in-with-code', json={'email': email, 'code': issued.code})
        assert response.status_code == 200, response.text

    @pytest.mark.parametrize('purpose', ['generate_signup_code', 'generate_password_reset_code'])
    async def test_the_other_codes(self, purpose):
        issued = await getattr(OTPService(), purpose)(f'scan-{uuid.uuid4().hex[:8]}@example.com')

        await _assert_nowhere(issued.code)

    async def test_a_handoff_code_and_the_token_behind_it(self):
        token = f'eyJ.{secrets.token_urlsafe(24)}.sig'
        code = await AuthHandoffDB().issue(token)

        await _assert_nowhere(code, token)
        assert await AuthHandoffDB().redeem(code) == token

    async def test_oauth_state_and_the_verifier_it_carries(self):
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        nonce = secrets.token_urlsafe(16)
        await OAuthStateDB().store(state, {'code_verifier': verifier, 'nonce': nonce})

        await _assert_nowhere(state, verifier, nonce)
        assert await OAuthStateDB().validate_and_consume(state) == {'code_verifier': verifier, 'nonce': nonce}

    async def test_an_authorization_code(self):
        pending = PendingAuthorization(
            client_id='scan-client',
            redirect_uri='https://codeindex.example.com/cb',
            code_challenge='c' * 43,
            user_id=str(uuid.uuid4()),
            org_id=str(uuid.uuid4()),
            session_issued_at=1,
        )
        code = await AuthorizationCodeDB().issue(pending)

        await _assert_nowhere(code)
        assert await AuthorizationCodeDB().redeem(code) == pending

    async def test_a_registered_nodes_api_key(self, client, monkeypatch):
        """Through the endpoint the cluster automation calls, then a
        heartbeat, which rewrites the entry."""
        monkeypatch.setattr(settings, 'node_registration_token', NODE_TOKEN)
        api_key = f'sk-node-{secrets.token_urlsafe(24)}'
        node_id = f'scan-{uuid.uuid4().hex[:8]}'
        headers = {'X-Igni-Node-Token': NODE_TOKEN}
        try:
            registration = {
                'id': node_id,
                'base_url': f'http://{node_id}.internal:8000/v1',
                'model': 'igni-1.0-27b',
                'api_key': api_key,
            }
            assert (await client.post('/nodes', json=registration, headers=headers)).status_code == 201
            assert (await client.post(f'/nodes/{node_id}/heartbeat', headers=headers)).status_code == 200

            await _assert_nowhere(api_key)

            # Routing still gets it.
            (node,) = [n for n in await NodeRegistry().list_nodes() if n.backend.id == node_id]
            assert node.backend.api_key == api_key
        finally:
            await NodeRegistry().deregister(node_id)
            await NodeRegistry().close()


class TestAnEntryThatWillNotDecrypt:
    """Sealed under a key since removed — or written in plaintext before
    F151, which is every entry live at the deploy. Treated as absent, said
    in the log, and never an exception on the sign-in or routing path."""

    @pytest.fixture
    def another_key(self, monkeypatch):
        def switch():
            monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())
            monkeypatch.setattr(settings, 'secret_encryption_keys_previous', '')

        return switch

    async def test_a_node_is_skipped_with_an_error_and_told_to_register_again(self, another_key, caplog):
        node_id = f'scan-{uuid.uuid4().hex[:8]}'
        registry = NodeRegistry()
        backend = InferenceBackend(
            id=node_id, base_url=f'http://{node_id}.internal/v1', model='m', api_key='sk-old'
        )
        try:
            await registry.register(backend)
            another_key()

            with caplog.at_level(logging.ERROR, logger='app.services.ai.node_registry'):
                listed = [n.backend.id for n in await registry.list_nodes()]

            assert node_id not in listed
            assert any(node_id in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
            # A heartbeat finds nothing to refresh, which is the node's
            # signal to register again — under the current key.
            assert await registry.heartbeat(node_id) is None
        finally:
            await registry.deregister(node_id)
            await registry.close()

    async def test_a_node_without_a_key_is_unaffected(self, another_key):
        node_id = f'scan-{uuid.uuid4().hex[:8]}'
        registry = NodeRegistry()
        try:
            await registry.register(InferenceBackend(id=node_id, base_url='http://n.internal/v1', model='m'))
            another_key()
            assert node_id in [n.backend.id for n in await registry.list_nodes()]
        finally:
            await registry.deregister(node_id)
            await registry.close()

    async def test_a_plaintext_entry_from_before_the_change_is_still_routed_then_sealed(self):
        """Entries live at the deploy carry the key in `backend`. They are
        read as they are and sealed by the next heartbeat's rewrite, so a
        rolling deploy does not drop every keyed node for a beat."""
        node_id = f'scan-{uuid.uuid4().hex[:8]}'
        registry = NodeRegistry()
        old = {
            'backend': {'id': node_id, 'base_url': 'http://n.internal/v1', 'models': ['m'], 'api_key': 'sk-legacy'},
            'registered_at': 1.0,
            'last_seen': 1.0,
        }
        redis_client = await registry._get_redis()
        try:
            await redis_client.set(f'igni:server:nodes:registered:{node_id}', json.dumps(old), ex=60)
            assert (await registry.get(node_id)).backend.api_key == 'sk-legacy'  # type: ignore[union-attr]

            await registry.heartbeat(node_id)

            await _assert_nowhere('sk-legacy')
            assert (await registry.get(node_id)).backend.api_key == 'sk-legacy'  # type: ignore[union-attr]
        finally:
            await registry.deregister(node_id)
            await registry.close()

    async def test_a_sign_in_code_is_discarded_and_a_new_one_issued(self, another_key, caplog):
        email = f'scan-{uuid.uuid4().hex[:8]}@example.com'
        first = await OTPService().generate_password_reset_code(email)
        another_key()

        with caplog.at_level(logging.ERROR, logger='app.services.auth.otp_service'):
            assert not await OTPService()._check_code(OtpPurpose.RESET, email, first.code)
        assert any('will not decrypt' in r.getMessage() for r in caplog.records)

    async def test_a_handoff_code_yields_nothing(self, another_key):
        code = await AuthHandoffDB().issue('the.jwt.value')
        another_key()

        assert await AuthHandoffDB().redeem(code) is None

    async def test_oauth_state_yields_nothing(self, another_key):
        state = secrets.token_urlsafe(32)
        await OAuthStateDB().store(state, {'code_verifier': 'v'})
        another_key()

        assert await OAuthStateDB().validate_and_consume(state) is None
