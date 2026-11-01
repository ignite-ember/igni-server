"""An erasure or an export reaches every service that holds a person's data.

The controller receives the request at igni. The data is spread across
the services registered with it, so each approved service is asked, for
the organisation whose administrator asked, with a token igni signs for
that one service. Erasure fails loudly — a service that did not confirm
is named, and running it again is safe — and export says what is
missing rather than omitting it.
"""

from __future__ import annotations

import uuid

import httpx
import jwt
import pytest

from app.services.auth.jwt_service import JWTService
from app.services.identity.key_ring import SigningKeys
from app.services.identity.service_subjects import ServiceSubjects
from tests.conftest import add_member, auth_header, create_org, create_user, make_token
from tests.registered_service import RegisteredService

pytestmark = pytest.mark.anyio


class _Services:
    """Every service's side of the wire, recorded."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.failing: set[str] = set()
        self.exports: dict[str, dict] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host in self.failing:
            return httpx.Response(503)
        if request.method == 'DELETE':
            return httpx.Response(204)
        return httpx.Response(200, json=self.exports.get(request.url.host, {'sections': []}))

    def claims(self, request: httpx.Request, audience: str) -> dict:
        """What the service checks, against igni's published key."""
        token = request.headers['authorization'].removeprefix('Bearer ')
        key = SigningKeys.ring().published.keys[0].as_dict()
        return jwt.decode(token, jwt.PyJWK(key), algorithms=['RS256'], audience=audience, issuer=JWTService().issuer)


@pytest.fixture
def services(monkeypatch):
    recorded = _Services()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(recorded.handler))

    monkeypatch.setattr(ServiceSubjects, '_client', _client)
    return recorded


@pytest.fixture
async def admin(db):
    person = await create_user(db, email=f'admin-{uuid.uuid4().hex[:6]}@example.com')
    org = await create_org(db, owner_user_id=str(person.user_id), slug=f'g-{uuid.uuid4().hex[:6]}')
    await add_member(db, str(org.id), str(person.user_id), role='admin')
    subject = await create_user(db, email='jane@example.com')
    await add_member(db, str(org.id), str(subject.user_id))
    return {
        'headers': auth_header(make_token(str(person.user_id), org_id=str(org.id), org_name='G')),
        'org_id': str(org.id),
        'subject': str(subject.user_id),
    }


async def _registered(name: str, host: str, *, state: str = 'approved') -> RegisteredService:
    service = RegisteredService(name=name, base_url=f'https://{host}')
    await service.approve(state=state)
    return service


class TestErasure:
    async def test_every_approved_service_is_told(self, client, admin, services):
        codeindex = await _registered('CodeIndex', 'codeindex.example.com')
        await _registered('Artifacts', 'artifacts.example.com')

        response = await client.post('/portal/erasure', headers=admin['headers'], json={'email': 'jane@example.com'})

        assert response.status_code == 200, response.text
        assert sorted(response.json()['services_told']) == ['Artifacts', 'CodeIndex']
        told = {r.url.host: r for r in services.calls}
        request = told['codeindex.example.com']
        assert request.method == 'DELETE'
        assert request.url.path == f'/v1/igni/subjects/{admin["subject"]}'
        claims = services.claims(request, codeindex.base_url)
        assert claims['token_use'] == 'igni'
        assert claims['org'] == admin['org_id']

    async def test_a_token_for_one_service_is_not_for_another(self, client, admin, services):
        codeindex = await _registered('CodeIndex', 'codeindex.example.com')
        artifacts = await _registered('Artifacts', 'artifacts.example.com')

        await client.post('/portal/erasure', headers=admin['headers'], json={'email': 'jane@example.com'})

        to_codeindex = next(r for r in services.calls if r.url.host == 'codeindex.example.com')
        with pytest.raises(jwt.InvalidAudienceError):
            services.claims(to_codeindex, artifacts.base_url)
        assert services.claims(to_codeindex, codeindex.base_url)

    async def test_a_service_that_does_not_confirm_is_named(self, client, admin, services):
        await _registered('CodeIndex', 'codeindex.example.com')
        services.failing.add('codeindex.example.com')

        response = await client.post('/portal/erasure', headers=admin['headers'], json={'email': 'jane@example.com'})

        assert response.status_code == 502
        assert 'CodeIndex' in response.json()['detail']
        assert 'Run it again' in response.json()['detail']

    async def test_a_pending_or_revoked_service_is_not_asked(self, client, admin, services):
        await _registered('Waiting', 'waiting.example.com', state='pending')
        await _registered('Gone', 'gone.example.com', state='revoked')

        response = await client.post('/portal/erasure', headers=admin['headers'], json={'email': 'jane@example.com'})

        assert response.json()['services_told'] == []
        assert services.calls == []

    async def test_nobody_to_erase_asks_nobody(self, client, admin, services):
        await _registered('CodeIndex', 'codeindex.example.com')

        response = await client.post(
            '/portal/erasure', headers=admin['headers'], json={'email': 'never-signed-up@example.com'}
        )

        assert response.status_code == 200
        assert services.calls == []


class TestExport:
    async def test_each_service_answers_under_its_own_name(self, client, admin, services):
        await _registered('CodeIndex', 'codeindex.example.com')
        services.exports['codeindex.example.com'] = {
            'sections': [{'name': 'linked_accounts', 'rows': [{'provider': 'github', 'login': 'jane'}]}]
        }

        response = await client.post(
            '/portal/subject-access', headers=admin['headers'], json={'email': 'jane@example.com'}
        )

        report = response.json()
        assert report['sections']['services'] == {
            'CodeIndex': {'linked_accounts': [{'provider': 'github', 'login': 'jane'}]}
        }

    async def test_a_silent_service_is_marked_missing(self, client, admin, services):
        await _registered('CodeIndex', 'codeindex.example.com')
        services.failing.add('codeindex.example.com')

        report = (
            await client.post('/portal/subject-access', headers=admin['headers'], json={'email': 'jane@example.com'})
        ).json()

        assert 'CodeIndex did not answer' in report['truncated']['CodeIndex']
