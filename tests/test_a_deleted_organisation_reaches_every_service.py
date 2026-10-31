"""A deleted organisation reaches every service, even one that was down.

Deleting writes one notice per approved service in the deletion's own
transaction; a round delivers them, with a token igni signs for that one
service, and keeps a notice it could not deliver for a later round.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import httpx
from jose import jwt
import pytest

from app.db.sql.organisation_deletion_notices import OrganisationDeletionNoticeDB
from app.db.sql.registered_clients import RegisteredClientDB
from app.services.auth.jwt_service import JWTService
from app.services.identity.key_ring import SigningKeys
from app.services.identity.organisation_deletions import LEASE, OrganisationDeletions
from tests.conftest import add_member, auth_header, create_org, create_user, make_token
from tests.registered_service import RegisteredService

pytestmark = pytest.mark.anyio


class _Services:
    """Every service's side of the wire, recorded."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.failing: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host in self.failing:
            return httpx.Response(503)
        return httpx.Response(204)

    def claims(self, request: httpx.Request, audience: str) -> dict:
        token = request.headers['authorization'].removeprefix('Bearer ')
        key = SigningKeys.ring().published.keys[0].as_dict()
        return jwt.decode(token, key, algorithms=['RS256'], audience=audience, issuer=JWTService().issuer)


@pytest.fixture
def services() -> _Services:
    return _Services()


@pytest.fixture
def deliveries(services: _Services) -> OrganisationDeletions:
    return OrganisationDeletions(http=httpx.AsyncClient(transport=httpx.MockTransport(services.handler)))


@pytest.fixture
async def admin(db):
    person = await create_user(db, email=f'admin-{uuid.uuid4().hex[:6]}@example.com')
    org = await create_org(db, owner_user_id=str(person.user_id), slug=f'g-{uuid.uuid4().hex[:6]}')
    await add_member(db, str(org.id), str(person.user_id), role='admin')
    return {
        'headers': auth_header(make_token(str(person.user_id), org_id=str(org.id), org_name='G')),
        'org_id': str(org.id),
    }


async def _registered(name: str, host: str, *, state: str = 'approved') -> RegisteredService:
    service = RegisteredService(name=name, base_url=f'https://{host}')
    await service.approve(state=state)
    return service


async def _delete(client, admin) -> None:
    response = await client.delete(f'/portal/organizations/{admin["org_id"]}', headers=admin['headers'])
    assert response.status_code == 200, response.text


def _later(**delta: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**delta)


class TestDeleting:
    async def test_every_approved_service_is_owed_the_news(self, client, admin):
        await _registered('CodeIndex', 'codeindex.example.com')
        await _registered('Artifacts', 'artifacts.example.com')
        await _registered('Waiting', 'waiting.example.com', state='pending')
        await _registered('Gone', 'gone.example.com', state='revoked')

        await _delete(client, admin)

        owed = await OrganisationDeletionNoticeDB().outstanding(admin['org_id'])
        names = {(await RegisteredClientDB().get(n.client_id)).name for n in owed}
        assert names == {'CodeIndex', 'Artifacts'}

    async def test_deleting_an_account_that_takes_its_organisation_owes_it_too(self, client, admin):
        await _registered('CodeIndex', 'codeindex.example.com')

        response = await client.delete('/portal/me', headers=admin['headers'])

        assert response.status_code == 200, response.text
        assert len(await OrganisationDeletionNoticeDB().outstanding(admin['org_id'])) == 1

    async def test_a_deployment_without_services_owes_nothing(self, client, admin):
        await _delete(client, admin)

        assert await OrganisationDeletionNoticeDB().outstanding() == []


class TestDelivering:
    async def test_each_service_is_told_with_a_token_for_it_alone(self, client, admin, services, deliveries):
        codeindex = await _registered('CodeIndex', 'codeindex.example.com')
        artifacts = await _registered('Artifacts', 'artifacts.example.com')
        await _delete(client, admin)

        round_ = await deliveries.deliver_due()

        assert sorted(name for _, name in round_.confirmed) == ['Artifacts', 'CodeIndex']
        told = {r.url.host: r for r in services.calls}
        request = told['codeindex.example.com']
        assert request.method == 'DELETE'
        assert request.url.path == f'/v1/igni/organisations/{admin["org_id"]}'
        claims = services.claims(request, codeindex.base_url)
        assert claims['token_use'] == 'igni'
        assert claims['org'] == admin['org_id']
        with pytest.raises(jwt.JWTClaimsError):
            services.claims(request, artifacts.base_url)
        assert await OrganisationDeletionNoticeDB().outstanding() == []

    async def test_a_service_that_was_down_is_told_when_it_is_back(self, client, admin, services, deliveries):
        await _registered('CodeIndex', 'codeindex.example.com')
        await _registered('Artifacts', 'artifacts.example.com')
        services.failing.add('codeindex.example.com')
        await _delete(client, admin)

        first = await deliveries.deliver_due()

        assert [name for _, name in first.unconfirmed] == ['CodeIndex']
        [owed] = await OrganisationDeletionNoticeDB().outstanding()
        assert owed.attempts == 1
        assert 'HTTP 503' in owed.last_error
        assert owed.next_attempt_at > datetime.now(timezone.utc)

        # Not due yet: nobody is called.
        services.calls.clear()
        assert (await deliveries.deliver_due()).confirmed == []
        assert services.calls == []

        services.failing.clear()
        later = await deliveries.deliver_due(now=_later(minutes=1))

        assert [name for _, name in later.confirmed] == ['CodeIndex']
        assert await OrganisationDeletionNoticeDB().outstanding() == []

    async def test_an_unreachable_service_is_unconfirmed_not_fatal(self, client, admin):
        await _registered('CodeIndex', 'codeindex.example.com')
        await _delete(client, admin)

        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError('down', request=request)

        deliveries = OrganisationDeletions(http=httpx.AsyncClient(transport=httpx.MockTransport(unreachable)))
        round_ = await deliveries.deliver_due()

        assert len(round_.unconfirmed) == 1
        [owed] = await OrganisationDeletionNoticeDB().outstanding()
        assert owed.last_error == 'ConnectError'

    async def test_telling_twice_is_harmless(self, client, admin, services, deliveries):
        await _registered('CodeIndex', 'codeindex.example.com')
        await _delete(client, admin)

        await deliveries.deliver_due()
        again = await deliveries.deliver_due(now=_later(hours=1))

        assert again.confirmed == [] and again.unconfirmed == []
        assert len(services.calls) == 1

    async def test_a_revoked_service_waits_and_a_removed_one_is_forgotten(self, client, admin, services, deliveries):
        codeindex = await _registered('CodeIndex', 'codeindex.example.com')
        await _delete(client, admin)
        await RegisteredClientDB().decide(codeindex.client_id, state='revoked', by_user_id=None)

        await deliveries.deliver_due()

        assert services.calls == []
        assert len(await OrganisationDeletionNoticeDB().outstanding()) == 1

        await RegisteredClientDB().remove(codeindex.client_id)
        assert await OrganisationDeletionNoticeDB().outstanding() == []

    async def test_a_taken_notice_is_not_taken_again_until_its_lease_runs_out(self, client, admin):
        await _registered('CodeIndex', 'codeindex.example.com')
        await _delete(client, admin)
        now = datetime.now(timezone.utc)

        first = await OrganisationDeletionNoticeDB().take_due(now=now, lease=LEASE, limit=10)
        second = await OrganisationDeletionNoticeDB().take_due(now=now, lease=LEASE, limit=10)
        after_lease = await OrganisationDeletionNoticeDB().take_due(
            now=now + LEASE + timedelta(seconds=1), lease=LEASE, limit=10
        )

        assert len(first) == 1
        assert second == []
        assert len(after_lease) == 1


class TestBackoff:
    def test_it_doubles_from_thirty_seconds_to_six_hours(self):
        delays = [OrganisationDeletions.retry_delay(n) for n in range(12)]

        assert delays[:4] == [timedelta(seconds=s) for s in (30, 60, 120, 240)]
        assert delays[-1] == timedelta(hours=6)
        assert OrganisationDeletions.retry_delay(10_000) == timedelta(hours=6)
