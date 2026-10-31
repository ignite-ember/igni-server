"""Apps: the producers that are not people (`docs/service-identity.md` §11).

An org admin registers an app on the portal with a name, an owner and
the app's public key. The app then obtains tokens as itself with
`client_credentials` and `private_key_jwt` — the same mechanism a
service uses — for its own organisation only. Its token carries
`kind: app` and no group; its extensions are its own list, off until an
org admin switches them on, and that list is all `GET /v1/extensions`
gives it.
"""

from __future__ import annotations

from jose import jwt
import pytest

from app.services.auth.jwt_service import JWTService
from tests.cloud_world import CloudOrg, make_super_admin, platform, shipped, token_for
from tests.conftest import make_token
from tests.registered_service import ExtensionBundle, RegisteredService

pytestmark = pytest.mark.anyio


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


class TestRegistering:
    async def test_an_org_admin_registers_an_app_and_it_is_listed(self, client, acme):
        app = await acme.app('Review bot')

        listed = (await client.get('/portal/apps', headers=acme.admin)).json()['apps']

        assert [(a['app_id'], a['name'], a['owner_user_id']) for a in listed] == [
            (app.client_id, 'Review bot', acme.admin_id)
        ]
        assert listed[0]['thumbprint'] == app.public.thumbprint()
        assert listed[0]['owner_email'] == 'admin@acme.com'

    async def test_a_member_cannot(self, client, acme):
        _, member = await acme.person('max@acme.com')
        app = RegisteredService(kind='app', base_url='')

        response = await client.post(
            '/portal/apps',
            json={'name': 'Bot', 'owner_user_id': acme.admin_id, 'jwks': {'keys': [app.public.as_dict()]}},
            headers=member,
        )

        assert response.status_code == 403

    async def test_the_owner_must_be_a_member(self, client, acme):
        app = RegisteredService(kind='app', base_url='')

        response = await client.post(
            '/portal/apps',
            json={'name': 'Bot', 'owner_user_id': 'nobody', 'jwks': {'keys': [app.public.as_dict()]}},
            headers=acme.admin,
        )

        assert response.status_code == 400

    async def test_a_key_another_client_holds_is_refused(self, client, acme):
        app = await acme.app()

        response = await client.post(
            '/portal/apps',
            json={'name': 'Copy', 'owner_user_id': acme.admin_id, 'jwks': {'keys': [app.public.as_dict()]}},
            headers=acme.admin,
        )

        assert response.status_code == 409

    async def test_a_private_key_is_refused(self, client, acme):
        app = RegisteredService(kind='app', base_url='')
        private = {**app.public.as_dict(), 'd': 'secret'}

        response = await client.post(
            '/portal/apps',
            json={'name': 'Bot', 'owner_user_id': acme.admin_id, 'jwks': {'keys': [private]}},
            headers=acme.admin,
        )

        assert response.status_code == 400

    async def test_another_organisation_does_not_see_it(self, client, acme):
        app = await acme.app()
        globex = await CloudOrg(client, slug='globex').create()

        assert (await client.get('/portal/apps', headers=globex.admin)).json()['apps'] == []
        assert (await client.get(f'/portal/apps/{app.client_id}', headers=globex.admin)).status_code == 404

    async def test_the_services_page_does_not_list_apps(self, client, acme):
        await acme.app()
        boss = await CloudOrg(client, slug='boss').create()
        await make_super_admin(boss.admin_id)

        listed = (await client.get('/portal/clients', headers=boss.admin)).json()

        assert listed == []

    async def test_renaming_changing_owner_and_rotating_keys(self, client, acme):
        app = await acme.app()
        max_id, _ = await acme.person('max@acme.com')
        rotated = RegisteredService(kind='app', base_url='')

        renamed = await client.patch(
            f'/portal/apps/{app.client_id}', json={'name': 'Release bot', 'owner_user_id': max_id}, headers=acme.admin
        )
        keys = await client.put(
            f'/portal/apps/{app.client_id}/keys',
            json={'jwks': {'keys': [rotated.public.as_dict()]}},
            headers=acme.admin,
        )

        assert renamed.json()['name'] == 'Release bot'
        assert renamed.json()['owner_user_id'] == max_id
        assert keys.json()['thumbprint'] == rotated.public.thumbprint()
        old = await client.post('/oauth/token', data=app.authenticated('client_credentials'))
        assert old.status_code == 401
        rotated.client_id = app.client_id
        await token_for(client, rotated)


class TestItsToken:
    async def test_an_app_gets_a_token_as_itself_for_its_organisation(self, client, acme):
        app = await acme.app()

        headers = await acme.app_token(app)

        claims = jwt.get_unverified_claims(headers['Authorization'].split()[1])
        assert claims['token_use'] == 'app'
        assert claims['kind'] == 'app'
        assert claims['sub'] == app.client_id
        assert claims['org'] == acme.org_id
        assert claims['aud'] == JWTService().issuer
        assert 'group' not in claims

    async def test_it_may_name_its_own_organisation_but_no_other(self, client, acme):
        app = await acme.app()
        globex = await CloudOrg(client, slug='globex').create()

        await token_for(client, app, organisation=acme.org_id)
        response = await client.post(
            '/oauth/token', data=app.authenticated('client_credentials', organisation=globex.org_id)
        )

        assert response.json()['error'] == 'invalid_target'

    async def test_a_removed_app_gets_nothing_and_its_token_stops_working(self, client, acme):
        app = await acme.app()
        headers = await acme.app_token(app)

        assert (await client.delete(f'/portal/apps/{app.client_id}', headers=acme.admin)).status_code == 204

        assert (await client.post('/oauth/token', data=app.authenticated('client_credentials'))).status_code == 401
        assert (await client.get('/extensions', headers=headers)).status_code == 401

    async def test_a_person_session_says_person(self, acme):
        claims = jwt.get_unverified_claims(make_token(acme.admin_id, org_id=acme.org_id))

        assert claims['kind'] == 'person'


class TestItsExtensions:
    async def test_off_until_switched_on_for_the_app(self, client, acme):
        await shipped(client, 'igni.codeindex')
        app = await acme.app()
        headers = await acme.app_token(app)

        before = (await client.get('/extensions', headers=headers)).json()
        await acme.switch_app(app.client_id, 'igni.codeindex')
        after = (await client.get('/extensions', headers=headers)).json()

        assert before['extensions'] == []
        assert after['producer'] == {'kind': 'app', 'id': app.client_id}
        assert after['group'] is None
        assert [(e['extension_id'], e['apps']) for e in after['extensions']] == [('igni.codeindex', [app.client_id])]
        assert (await client.get('/extensions/igni.codeindex/bundle', headers=headers)).status_code == 200

    async def test_a_group_switch_does_not_reach_an_app_nor_the_other_way(self, client, acme):
        await shipped(client, 'igni.codeindex')
        await shipped(client, 'igni.artifacts')
        legal = await acme.group('Legal')
        _, person = await acme.person('ada@acme.com', legal)
        app = await acme.app()
        await acme.switch_group(legal, 'igni.codeindex')
        await acme.switch_app(app.client_id, 'igni.artifacts')

        mine = (await client.get('/extensions', headers=person)).json()
        its = (await client.get('/extensions', headers=await acme.app_token(app))).json()

        assert [e['extension_id'] for e in mine['extensions']] == ['igni.codeindex']
        assert mine['producer']['kind'] == 'person'
        assert [e['extension_id'] for e in its['extensions']] == ['igni.artifacts']
        assert (
            await client.get('/extensions/igni.codeindex/bundle', headers=await acme.app_token(app))
        ).status_code == 404

    async def test_the_portal_lists_and_switches_an_apps_extensions(self, client, acme):
        await shipped(client, 'igni.codeindex')
        app = await acme.app()

        await acme.switch_app(app.client_id, 'igni.codeindex')
        listed = (await client.get(f'/portal/apps/{app.client_id}/extensions', headers=acme.admin)).json()
        await acme.switch_app(app.client_id, 'igni.codeindex', enabled=False)
        off = (await client.get(f'/portal/apps/{app.client_id}/extensions', headers=acme.admin)).json()

        assert [(e['extension_id'], e['enabled']) for e in listed['extensions']] == [('igni.codeindex', True)]
        assert [(e['extension_id'], e['enabled']) for e in off['extensions']] == [('igni.codeindex', False)]

    async def test_only_an_approved_extension_can_be_switched_on(self, client, acme):
        app = await acme.app()

        response = await client.put(
            f'/portal/apps/{app.client_id}/extensions/igni.nothing', json={'enabled': True}, headers=acme.admin
        )

        assert response.status_code == 404


class TestPlatformClients:
    async def test_a_platform_client_gets_a_token_naming_no_organisation(self, client):
        headers = await platform(client)

        claims = jwt.get_unverified_claims(headers['Authorization'].split()[1])
        assert claims['token_use'] == 'platform'
        assert 'org' not in claims

    async def test_a_platform_token_is_not_a_service_token(self, client, acme):
        headers = await platform(client)

        assert (await client.get('/directory/organisation', headers=headers)).status_code == 401

    async def test_neither_an_app_nor_a_platform_client_ships_an_extension(self, client, acme):
        app = await acme.app()
        bundle = ExtensionBundle.of({'id': 'igni.bot', 'version': '1.0.0', 'name': 'bot'})
        response = await client.put(
            f'/clients/{app.client_id}/extension',
            content=bundle.archive,
            headers={
                'Content-Type': 'application/gzip',
                'X-Igni-Bundle-Signature': bundle.signed_by(app, extension='igni.bot', version='1.0.0'),
            },
        )

        assert response.status_code == 400
