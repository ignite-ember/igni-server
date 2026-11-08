"""A service registers itself, and a super admin approves it once.

Nobody types a secret into anything. The service sends its public key,
prints the key's thumbprint in its own log, and waits; the super admin
compares the thumbprint the portal shows with the one in the log and
approves. Until then the registration grants nothing — the token
endpoint says so — and a revocation takes effect at once.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.config import settings
from app.services.identity import registry
from app.services.identity.jwk import RsaKeyPair
from tests.conftest import audit_rows, auth_header, create_user, make_token, TEST_DB_URL_SYNC
from tests.registered_service import RegisteredService

pytestmark = pytest.mark.anyio


def _decided_by(client_id: str) -> str | None:
    """Synchronous: the `db` fixture and the `client` run on different loops."""
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text('SELECT decided_by_user_id FROM registered_clients WHERE client_id = :c'), {'c': client_id}
            ).scalar()
    finally:
        engine.dispose()


async def _super_admin(db):
    user = await create_user(db, email='owner@example.com', is_super_admin=True)
    return auth_header(make_token(str(user.user_id)))


class TestRegistering:
    async def test_it_waits_for_a_decision(self, client):
        service = RegisteredService()

        response = await client.post('/clients', json=service.registration())

        assert response.status_code == 201
        body = response.json()
        assert body['state'] == 'pending'
        assert body['client_uri'] == 'https://codeindex.example.com'
        assert body['token_endpoint_auth_method'] == 'private_key_jwt'
        assert body['thumbprint'] == service.public.thumbprint()

    async def test_registering_again_is_the_same_registration(self, client):
        """A service registers on every start; restarts must not pile up."""
        service = RegisteredService()

        first = (await client.post('/clients', json=service.registration())).json()
        second = (await client.post('/clients', json=service.registration(client_name='Renamed'))).json()

        assert first['client_id'] == second['client_id']

    async def test_registering_again_cannot_undo_a_revocation(self, client, db):
        service = RegisteredService()
        await service.approve(state='revoked')

        body = (await client.post('/clients', json=service.registration())).json()

        assert body['client_id'] == service.client_id
        assert body['state'] == 'revoked'

    async def test_a_private_key_is_refused(self, client):
        service = RegisteredService()
        leaked = {**service.public.as_dict(), 'd': 'the-private-exponent'}

        response = await client.post('/clients', json=service.registration(jwks={'keys': [leaked]}))

        assert response.status_code == 400
        assert response.json()['detail']['error'] == 'invalid_client_metadata'
        assert 'private key' in response.json()['detail']['error_description']

    async def test_every_client_says_where_it_is(self, client):
        """Its URL is the audience of every token a person gets for it."""
        response = await client.post('/clients', json=RegisteredService().registration(client_uri=''))

        assert response.status_code == 400
        assert 'client_uri' in response.json()['detail']['error_description']

    async def test_the_url_is_written_one_way(self, client):
        """Services compare a token's audience with their URL as a string."""
        response = await client.post(
            '/clients', json=RegisteredService().registration(client_uri='HTTPS://CodeIndex.Example.com/')
        )

        assert response.json()['client_uri'] == 'https://codeindex.example.com'

    async def test_a_path_on_another_host_is_refused(self, client):
        """A service has its own hostname; a path would share somebody
        else's namespace, including the subject routes igni calls."""
        response = await client.post(
            '/clients', json=RegisteredService().registration(client_uri='https://igni.example.com/artifacts')
        )

        assert response.status_code == 400
        assert 'origin of its own' in response.json()['detail']['error_description']

    async def test_igni_s_own_address_is_refused(self, client):
        response = await client.post(
            '/clients', json=RegisteredService().registration(client_uri=settings.public_base_url)
        )

        assert response.status_code == 400
        assert "igni's own address" in response.json()['detail']['error_description']

    async def test_another_port_on_this_machine_is_its_own_origin(self, client, monkeypatch):
        """How a local stack runs: a service on its own port beside igni.
        `*.localhost` is this machine, so plain http is allowed even outside dev."""
        monkeypatch.setattr(settings, 'app_env', 'prod')
        response = await client.post(
            '/clients', json=RegisteredService().registration(client_uri='http://my-service.localhost:8100')
        )

        assert response.status_code == 201, response.text

    async def test_an_app_does_not_register_itself(self, client):
        """An org admin registers an app on the portal, in their organisation."""
        response = await client.post('/clients', json=RegisteredService(kind='app').registration())

        assert response.status_code in (400, 422)

    async def test_a_platform_client_registers_the_same_way(self, client):
        response = await client.post('/clients', json=RegisteredService(kind='platform').registration())

        assert response.status_code == 201
        assert response.json()['state'] == 'pending'

    async def test_plain_http_is_refused_outside_this_machine(self, client, monkeypatch):
        monkeypatch.setattr(settings, 'app_env', 'prod')
        response = await client.post(
            '/clients', json=RegisteredService().registration(client_uri='http://codeindex.example.com')
        )

        assert response.status_code == 400

    async def test_the_waiting_room_is_bounded(self, client, monkeypatch):
        """Registration is unauthenticated, so it is capped instead."""
        monkeypatch.setattr(registry, 'MAX_PENDING', 2)
        for _ in range(2):
            assert (await client.post('/clients', json=RegisteredService().registration())).status_code == 201

        response = await client.post('/clients', json=RegisteredService().registration())

        assert response.status_code == 429

    async def test_it_is_recorded(self, client):
        await client.post('/clients', json=RegisteredService().registration())

        assert audit_rows(route='/v1/clients')


class TestDeciding:
    async def test_only_a_super_admin_decides(self, client, db):
        user = await create_user(db, email='someone@example.com')
        registered = (await client.post('/clients', json=RegisteredService().registration())).json()

        response = await client.post(
            f'/portal/clients/{registered["client_id"]}/approve',
            headers=auth_header(make_token(str(user.user_id))),
        )

        assert response.status_code == 403

    async def test_the_portal_shows_the_thumbprint_to_compare(self, client, db):
        headers = await _super_admin(db)
        service = RegisteredService()
        await client.post('/clients', json=service.registration())

        listed = (await client.get('/portal/clients', headers=headers)).json()

        assert [c['thumbprint'] for c in listed] == [service.public.thumbprint()]

    async def test_approving_lets_it_obtain_tokens(self, client, db):
        headers = await _super_admin(db)
        service = RegisteredService()
        registered = (await client.post('/clients', json=service.registration())).json()
        service.client_id = registered['client_id']

        approved = await client.post(f'/portal/clients/{service.client_id}/approve', headers=headers)

        assert approved.json()['state'] == 'approved'
        assert _decided_by(service.client_id) is not None

    async def test_the_decision_is_audited_with_what_was_compared(self, client, db):
        headers = await _super_admin(db)
        service = RegisteredService()
        registered = (await client.post('/clients', json=service.registration())).json()

        await client.post(f'/portal/clients/{registered["client_id"]}/approve', headers=headers)

        rows = audit_rows(target_id=registered['client_id'])
        assert rows
        assert rows[0]['detail']['decision'] == 'approved'
        assert rows[0]['detail']['thumbprint'] == service.public.thumbprint()

    async def test_revoking_and_removing(self, client, db):
        headers = await _super_admin(db)
        registered = (await client.post('/clients', json=RegisteredService().registration())).json()
        client_id = registered['client_id']

        revoked = await client.post(f'/portal/clients/{client_id}/revoke', headers=headers)
        removed = await client.delete(f'/portal/clients/{client_id}', headers=headers)

        assert revoked.json()['state'] == 'revoked'
        assert removed.status_code == 204
        assert (await client.get('/portal/clients', headers=headers)).json() == []

    async def test_an_unknown_client_is_404(self, client, db):
        headers = await _super_admin(db)

        response = await client.post('/portal/clients/no-such-client/approve', headers=headers)

        assert response.status_code == 404

    @pytest.mark.parametrize(
        ('method', 'path', 'detail'),
        [
            ('GET', '/portal/clients/no-such-client', 'No such client'),
            ('POST', '/portal/clients/no-such-client/approve', 'No such client'),
            ('POST', '/portal/clients/no-such-client/revoke', 'No such client'),
            ('DELETE', '/portal/clients/no-such-client', 'No such client'),
            ('POST', '/portal/clients/no-such-client/held-update/approve', 'This service has no update waiting'),
            ('POST', '/portal/clients/no-such-client/held-update/reject', 'This service has no update waiting'),
        ],
    )
    async def test_every_route_says_so_in_its_own_words(self, client, db, method, path, detail):
        headers = await _super_admin(db)

        response = await client.request(method, path, headers=headers)

        assert (response.status_code, response.json()['detail']) == (404, detail)
        assert audit_rows(target_id='no-such-client') == [], 'a refused decision was audited as made'

    async def test_a_client_with_no_update_waiting_has_none_to_decide(self, client, db):
        headers = await _super_admin(db)
        registered = (await client.post('/clients', json=RegisteredService().registration())).json()

        for decision in ('approve', 'reject'):
            response = await client.post(
                f'/portal/clients/{registered["client_id"]}/held-update/{decision}', headers=headers
            )

            assert (response.status_code, response.json()['detail']) == (404, 'This service has no update waiting')


class TestTwoKeysOneThumbprint:
    def test_different_keys_have_different_thumbprints(self):
        assert RsaKeyPair.generate().public_jwk().thumbprint() != RsaKeyPair.generate().public_jwk().thumbprint()
