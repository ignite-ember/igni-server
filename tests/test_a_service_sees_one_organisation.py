"""The directory answers about the token's organisation, and no other.

The organisation is never a parameter: it is the `org` the service token
carries. A token for one organisation is no way into another, whatever
the request names — and a revoked service is refused at once, not when
its token runs out.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.tenant_registry import TenantRegistryDB
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.auth.token_claims import CustomClaims
from tests.conftest import add_member, auth_header, create_org, create_user, make_token
from tests.registered_service import RegisteredService

pytestmark = pytest.mark.anyio


@pytest.fixture
async def two_orgs(db):
    ada = await create_user(db, email='ada@acme.com', full_name='Ada Admin', is_super_admin=True)
    max_ = await create_user(db, email='max@acme.com', full_name='Max Member')
    left = await create_user(db, email='left@acme.com', full_name='Lena Left')
    acme = await create_org(db, owner_user_id=str(ada.user_id), slug='acme', display_name='Acme', domains=('acme.com',))
    await add_member(db, str(acme.id), str(max_.user_id))
    await add_member(db, str(acme.id), str(left.user_id), status='deactivated')
    gus = await create_user(db, email='gus@globex.com', full_name='Gus Globex')
    globex = await create_org(db, owner_user_id=str(gus.user_id), slug='globex', display_name='Globex')
    service = RegisteredService()
    await service.approve()
    return {
        'acme': str(acme.id),
        'globex': str(globex.id),
        'ada': str(ada.user_id),
        'max': str(max_.user_id),
        'left': str(left.user_id),
        'gus': str(gus.user_id),
        'service': service,
    }


async def _service_token(client, world, org: str) -> dict[str, str]:
    response = await client.post(
        '/oauth/token', data=world['service'].authenticated('client_credentials', organisation=org)
    )
    return auth_header(response.json()['access_token'])


class TestOneOrganisation:
    async def test_the_organisation_and_its_number(self, client, two_orgs):
        headers = await _service_token(client, two_orgs, two_orgs['acme'])

        body = (await client.get('/directory/organisation', headers=headers)).json()

        assert body['org_id'] == two_orgs['acme']
        assert body['name'] == 'Acme'
        assert body['domains'] == ['acme.com']
        assert isinstance(body['tenant_number'], int)

    async def test_the_number_is_the_one_the_registry_gave_it(self, client, two_orgs):
        """Its own number, made on first use if it had none, not a count of anything."""
        await TenantRegistryDB().database_of(two_orgs['acme'])
        await TenantRegistryDB().database_of(two_orgs['globex'])
        headers = await _service_token(client, two_orgs, two_orgs['acme'])

        body = (await client.get('/directory/organisation', headers=headers)).json()

        assert body['tenant_number'] == 1 == await TenantRegistryDB().number_of(two_orgs['acme'])

    async def test_its_members_and_nobody_elses(self, client, two_orgs):
        headers = await _service_token(client, two_orgs, two_orgs['acme'])

        people = (await client.get('/directory/members', headers=headers)).json()['people']

        assert sorted(p['email'] for p in people) == ['ada@acme.com', 'max@acme.com']
        ada = next(p for p in people if p['email'] == 'ada@acme.com')
        assert ada['role'] == 'admin'
        assert ada['super_admin'] is True

    async def test_former_members_on_request(self, client, two_orgs):
        headers = await _service_token(client, two_orgs, two_orgs['acme'])

        people = (await client.get('/directory/members', params={'include_former': True}, headers=headers)).json()

        left = next(p for p in people['people'] if p['email'] == 'left@acme.com')
        assert left['status'] == 'deactivated'

    async def test_prefix_search(self, client, two_orgs):
        headers = await _service_token(client, two_orgs, two_orgs['acme'])

        people = (await client.get('/directory/members', params={'query': 'Ma'}, headers=headers)).json()['people']

        assert [p['email'] for p in people] == ['max@acme.com']

    async def test_somebody_from_another_organisation_is_not_found(self, client, two_orgs):
        headers = await _service_token(client, two_orgs, two_orgs['acme'])

        response = await client.get(f'/directory/people/{two_orgs["gus"]}', headers=headers)

        assert response.status_code == 404

    async def test_a_former_member_is_still_named(self, client, two_orgs):
        """An author who left still wrote what they wrote."""
        headers = await _service_token(client, two_orgs, two_orgs['acme'])

        response = await client.get(f'/directory/people/{two_orgs["left"]}', headers=headers)

        assert response.json()['name'] == 'Lena Left'

    async def test_resolving_names_only_this_organisations_people(self, client, two_orgs):
        headers = await _service_token(client, two_orgs, two_orgs['acme'])

        response = await client.post(
            '/directory/people/resolve',
            json={'identities': ['MAX@acme.com', two_orgs['ada'], 'gus@globex.com', two_orgs['gus']]},
            headers=headers,
        )

        assert sorted(p['email'] for p in response.json()['people']) == ['ada@acme.com', 'max@acme.com']

    async def test_the_other_organisations_token_sees_the_other_organisation(self, client, two_orgs):
        headers = await _service_token(client, two_orgs, two_orgs['globex'])

        people = (await client.get('/directory/members', headers=headers)).json()['people']

        assert [p['email'] for p in people] == ['gus@globex.com']


class TestOnlyAServiceToken:
    async def test_no_token(self, client, two_orgs):
        assert (await client.get('/directory/members')).status_code == 401

    async def test_a_persons_session(self, client, two_orgs):
        headers = auth_header(make_token(two_orgs['ada'], org_id=two_orgs['acme']))

        assert (await client.get('/directory/members', headers=headers)).status_code == 401

    async def test_a_user_token_for_the_service(self, client, two_orgs):
        """Minted for the service to accept — not for igni's directory."""
        tokens = JWTService()
        user_token = tokens.sign(
            subject=two_orgs['ada'],
            audience=two_orgs['service'].client_id,
            use=TokenUse.USER,
            lifetime=timedelta(minutes=5),
            claims=CustomClaims(org=two_orgs['acme']),
        )

        response = await client.get('/directory/members', headers=auth_header(user_token))

        assert response.status_code == 401

    async def test_a_revoked_service_is_refused_at_once(self, client, two_orgs):
        headers = await _service_token(client, two_orgs, two_orgs['acme'])
        await RegisteredClientDB().decide(two_orgs['service'].client_id, state='revoked', by_user_id=None)

        response = await client.get('/directory/members', headers=headers)

        assert response.status_code == 401
