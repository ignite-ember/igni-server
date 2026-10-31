"""igni, running, reaches nothing outside the customer's network.

`test_nothing_phones_home.py` proves no unexplained host is *named* in
`app/`. It cannot see a host built at runtime, a default buried in a
dependency, or a library that calls home on first use, and
`docs/NETWORK_DIAGRAM.md` (Gaps, item 2) says so. This file closes that:
it boots the application with its lifespan, drives it through the things
a deployment does in a day, and fails if any connection, name lookup or
child process aimed outside the network was so much as attempted.

Every peer igni needs — the identity provider, an inference node, a paid
module — is a real HTTP server on 127.0.0.1 (`tests/loopback_peers.py`),
so igni's own HTTP clients open real sockets and the guard watches them
do it. Postgres and Redis are the suite's own, on localhost. Each test
also checks the peer was actually called, because "no external
connection" is also what a test that connected to nothing would report.

What this does not cover is in `tests/egress_guard.py`: traffic from a
native extension's own networking, and anything a child process does once
started. And one about the loop: production runs under uvicorn with
uvloop (`uvicorn[standard]`), which resolves and connects inside libuv
where the audit hook cannot hear it. Here the app runs in-process on the
stdlib asyncio loop, so the guard sees every call; *which* hosts are
called is the application's choice, not the loop's, so the result
carries over. The portal is a static bundle served by nginx and loads no analytics
tag; the source scan in `test_nothing_phones_home.py` keeps it that way.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
import json
from urllib.parse import parse_qs, urlsplit
import uuid

import httpx
import pytest

from app.services.ai.backend_provider import InferenceBackendProvider
from app.services.ai.pool_manager import PoolManager
from app.utils.singleton import SingletonMeta
from tests.conftest import add_member, auth_header, create_org, create_user, make_token
from tests.egress_guard import EgressGuard, EgressLog, ExternalNameRefused, InternalNetwork
from tests.loopback_peers import StubIdentityProvider, StubModelNode, StubModuleService
from tests.registered_service import RegisteredService

FRONTEND = 'https://igni.acme.internal'
PERSON = 'dev@acme.example'


class Peers:
    """Everything igni talks to in a working deployment, on loopback."""

    def __init__(self) -> None:
        self.idp = StubIdentityProvider(email=PERSON)
        self.node = StubModelNode()
        self.module = StubModuleService()


@pytest.fixture
def peers() -> Iterator[Peers]:
    found = Peers()
    with found.idp, found.node, found.module:
        yield found


@pytest.fixture
def deployment(monkeypatch: pytest.MonkeyPatch, peers: Peers) -> Iterator[None]:
    """igni configured the way a customer configures it, pointed at the peers."""
    from app.config import settings

    monkeypatch.setattr(settings, 'oidc_issuer', peers.idp.url)
    monkeypatch.setattr(settings, 'oidc_client_id', StubIdentityProvider.CLIENT_ID)
    monkeypatch.setattr(settings, 'oidc_client_secret', StubIdentityProvider.CLIENT_SECRET)
    monkeypatch.setattr(settings, 'oauth_redirect_base_url', FRONTEND)
    monkeypatch.setattr(settings, 'oauth_frontend_base_url', FRONTEND)
    monkeypatch.setattr(settings, 'inference_backends_path', '')
    backends = [
        {
            'id': 'loopback-node',
            'base_url': f'{peers.node.url}/v1',
            'model': StubModelNode.MODEL,
            'max_concurrent': 4,
            'pools': ['chat', 'codeindex'],
        }
    ]
    monkeypatch.setattr(settings, 'inference_backends', json.dumps(backends))
    # A singleton: a provider an earlier test built would otherwise route
    # this one's chat to wherever that test pointed it.
    SingletonMeta._instances.pop(PoolManager, None)
    PoolManager(provider=InferenceBackendProvider())
    yield
    SingletonMeta._instances.pop(PoolManager, None)


@pytest.fixture
async def running(app, deployment: None) -> AsyncIterator[EgressLog]:
    """The application started with its lifespan, inside the guard.

    Startup and shutdown run inside the guard as well as the requests:
    a library that reaches out on import or at first use, or a
    background task the lifespan starts, is exactly what this is for.
    """
    with EgressGuard(network=InternalNetwork(), watch_subprocesses=True).watching() as log:
        async with app.router.lifespan_context(app):
            yield log


@pytest.fixture
async def member(db) -> dict[str, str]:
    person = await create_user(db, email=f'member-{uuid.uuid4().hex[:6]}@acme.example')
    org = await create_org(db, owner_user_id=str(person.user_id), slug=f'acme-{uuid.uuid4().hex[:6]}')
    await add_member(db, str(org.id), str(person.user_id), role='admin')
    return {
        'org_id': str(org.id),
        **auth_header(make_token(str(person.user_id), org_id=str(org.id), org_name='Acme')),
    }


def _headers(member: dict[str, str]) -> dict[str, str]:
    return {'Authorization': member['Authorization']}


class TestStartingAndServing:
    async def test_it_starts_and_says_it_is_ready(self, running: EgressLog, client: httpx.AsyncClient):
        for path in ('/health', '/ready', '/version', '/.well-known/openid-configuration', '/.well-known/jwks.json'):
            response = await client.get(f'http://test{path}')
            assert response.status_code == 200, (path, response.text)

        assert not running.attempts, running.report()


class TestSigningIn:
    async def test_a_person_signs_in_through_the_customers_identity_provider(
        self, running: EgressLog, client: httpx.AsyncClient, peers: Peers
    ):
        start = await client.get('/auth/oidc')
        assert start.status_code == 302, start.text
        authorize = urlsplit(start.headers['location'])
        assert f'{authorize.scheme}://{authorize.netloc}' == peers.idp.url
        query = parse_qs(authorize.query)

        # The browser's leg: the person signs in at the IdP, which sends
        # them back with a code bound to igni's nonce.
        code = peers.idp.will_sign_in(nonce=query['nonce'][0])
        back = await client.get('/auth/oidc/callback', params={'code': code, 'state': query['state'][0]})
        assert back.status_code == 302, back.text
        landing = urlsplit(back.headers['location'])
        assert landing.netloc == urlsplit(FRONTEND).netloc

        exchanged = await client.post('/auth/exchange', json={'code': parse_qs(landing.query)['code'][0]})
        assert exchanged.status_code == 200, exchanged.text

        assert {'/.well-known/openid-configuration', '/token', '/jwks'} <= set(peers.idp.paths())
        assert not running.attempts, running.report()


class TestChatting:
    async def test_a_turn_goes_to_the_customers_node_and_nowhere_else(
        self, running: EgressLog, client: httpx.AsyncClient, peers: Peers, member: dict[str, str]
    ):
        models = await client.get('/chat/models', headers=_headers(member))
        assert models.status_code == 200, models.text
        assert [m['id'] for m in models.json()['models']] == [StubModelNode.MODEL]

        turn = {'messages': [{'role': 'user', 'content': 'hi'}]}
        whole = await client.post('/chat/completions', headers=_headers(member), json=turn)
        assert whole.status_code == 200, whole.text
        assert whole.json()['choices'][0]['message']['content'] == 'hello'

        streamed = await client.post('/chat/completions', headers=_headers(member), json={**turn, 'stream': True})
        assert streamed.status_code == 200, streamed.text
        assert '"content": "lo"' in streamed.text or '"content":"lo"' in streamed.text

        assert peers.node.paths().count('/v1/chat/completions') == 2
        assert not running.attempts, running.report()


class TestModules:
    async def test_a_module_gets_its_token_and_its_extension_list(
        self, running: EgressLog, client: httpx.AsyncClient, peers: Peers, member: dict[str, str]
    ):
        service = RegisteredService(name='CodeIndex', base_url=peers.module.url)
        await service.approve()

        token = await client.post(
            '/oauth/token', data=service.authenticated('client_credentials', organisation=member['org_id'])
        )
        assert token.status_code == 200, token.text
        extensions = await client.get('/extensions', headers=_headers(member))
        assert extensions.status_code == 200, extensions.text

        assert not running.attempts, running.report()

    async def test_a_deleted_organisation_reaches_the_module_and_nothing_else(
        self, running: EgressLog, client: httpx.AsyncClient, peers: Peers, member: dict[str, str]
    ):
        service = RegisteredService(name='Artifacts', base_url=peers.module.url)
        await service.approve()

        deleted = await client.delete(f'/portal/organizations/{member["org_id"]}', headers=_headers(member))
        assert deleted.status_code == 200, deleted.text

        # Delivered by the lifespan's own background loop, which the
        # deletion nudges. Waited for rather than called, so it is the
        # loop a deployment runs that the guard is watching.
        expected = f'/v1/igni/organisations/{member["org_id"]}'
        for _ in range(100):
            if expected in peers.module.paths():
                break
            await asyncio.sleep(0.05)
        assert expected in peers.module.paths(), peers.module.paths()
        assert not running.attempts, running.report()


@pytest.mark.reaches_outside_on_purpose
class TestTheGuardIsWatching:
    """A rule that cannot fail is worse than none. Each case here is an
    attempt the guard must see, made the way igni's own code makes one."""

    async def test_it_sees_an_http_client_reach_for_a_public_host(self):
        with EgressGuard(network=InternalNetwork()).watching() as log:
            with pytest.raises(httpx.ConnectError):
                async with httpx.AsyncClient(timeout=2) as http:
                    await http.get('https://pypi.org/simple/igni/')

        assert [a.destination for a in log.attempts] == ['pypi.org']

    async def test_it_records_an_attempt_the_caller_swallowed(self):
        """Code built to work offline catches the error and carries on;
        the log still has the attempt, and the log is what is asserted."""
        with EgressGuard(network=InternalNetwork()).watching() as log:
            try:
                await asyncio.get_running_loop().getaddrinfo('telemetry.example.com', 443)
            except ExternalNameRefused:
                pass

        assert [a.destination for a in log.attempts] == ['telemetry.example.com']

    async def test_it_sees_a_child_process_given_a_public_url(self):
        with EgressGuard(network=InternalNetwork(), watch_subprocesses=True).watching() as log:
            with pytest.raises(OSError):
                await asyncio.create_subprocess_exec('git', 'ls-remote', 'https://github.com/example/example')

        assert [a.destination.split(' ')[0] for a in log.attempts] == ['github.com']

    def test_the_cloud_metadata_address_is_outside(self):
        """Link-local is not "private": 169.254.169.254 is the metadata
        service, the address a dependency is likeliest to probe unasked."""
        network = InternalNetwork()

        assert not network.contains('169.254.169.254')
        assert not network.contains('100.64.0.1')
        assert network.contains('10.1.2.3') and network.contains('::1') and network.contains('postgres')
