"""A service hands igni its extension; igni shows it, then distributes it.

The extension on a laptop must be the one built for the service that is
deployed, and must be what the super admin was shown. So:

* the service signs the bundle with the key it registered, over the
  archive's bytes, and igni refuses anything that does not verify;
* a pending service may upload — the admin needs to see what they are
  approving — but nothing is distributed until it is approved;
* the portal's description is read out of the archive itself, including
  the agents of igni's it stands in for;
* two approved services cannot ship one extension id.
"""

from __future__ import annotations

import hashlib

import pytest

from app.config import settings
from app.middlewares.body_size_limit import BodySizeLimitMiddleware
from app.services.identity.extension_bundle import MAX_ARCHIVE_BYTES
from tests.conftest import auth_header, create_user, make_token, the_app
from tests.entitled_person import EntitledPerson
from tests.registered_service import ExtensionBundle, RegisteredService

pytestmark = pytest.mark.anyio

MANIFEST = {
    'id': 'igni.codeindex',
    'version': '1.2.0',
    'name': 'CodeIndex',
    'description': 'Semantic code search',
    'vendor': 'Ignite Ember',
    'permissions': {
        'workspace': 'read',
        'tools': [
            {
                'name': 'igni.codeindex.search',
                'annotations': {
                    'readOnlyHint': True,
                    'destructiveHint': False,
                    'idempotentHint': True,
                    'openWorldHint': False,
                },
            }
        ],
    },
    'contributes': {
        'pages': [{'kind': 'igni.codeindex', 'label': 'CodeIndex', 'sidebar': True}],
        'commands': [{'command': '/codeindex', 'desc': 'semantic code search'}],
        'prompt': {
            'overrides': [
                {
                    'target': 'agent:explorer',
                    'sections': ['body'],
                    'source': 'file',
                    'from': 'plugin/overrides/explorer.md',
                }
            ]
        },
        'plugin': 'plugin',
    },
}
EXPLORER = (
    '---\nname: explorer\ndescription: Explores with the index\n---\n'
    '<!-- section:body -->\nUse CodeIndex.\n<!-- /section:body -->\n'
)
HELPER = '---\nname: index-helper\ndescription: Its own agent\n---\nHelp.\n'
SKILL = '---\nname: reindex\ndescription: Rebuild the index\n---\nSteps.\n'


def _bundle(**manifest_overrides: object) -> ExtensionBundle:
    return ExtensionBundle.of(
        {**MANIFEST, **manifest_overrides},
        **{
            'plugin/overrides/explorer.md': EXPLORER,
            'plugin/agents/index-helper.md': HELPER,
            'plugin/skills/reindex/SKILL.md': SKILL,
            'src/igni_codeindex/__init__.py': '',
        },
    )


async def _upload(client, service: RegisteredService, bundle: ExtensionBundle, signature: str | None = None):
    return await client.put(
        f'/clients/{service.client_id}/extension',
        content=bundle.archive,
        headers={
            'Content-Type': 'application/gzip',
            'X-Igni-Bundle-Signature': signature
            or bundle.signed_by(service, extension=MANIFEST['id'], version=MANIFEST['version']),
        },
    )


async def _super_admin(db) -> dict[str, str]:
    user = await create_user(db, email='owner@example.com', is_super_admin=True)
    return auth_header(make_token(str(user.user_id)))


class TestUploading:
    async def test_a_signed_bundle_is_accepted(self, client):
        service = RegisteredService()
        await service.approve()
        bundle = _bundle()

        response = await _upload(client, service, bundle)

        assert response.status_code == 200, response.text
        assert response.json() == {
            'extension_id': 'igni.codeindex',
            'version': '1.2.0',
            'sha256': hashlib.sha256(bundle.archive).hexdigest(),
            'held': False,
            'reason': None,
            'added': [],
        }

    async def test_a_service_can_ask_whether_it_needs_to_upload(self, client):
        """A bundle is tens of megabytes; a restart should not resend it."""
        service = RegisteredService()
        await service.approve()
        assert (await client.get(f'/clients/{service.client_id}/extension')).status_code == 404
        bundle = _bundle()
        await _upload(client, service, bundle)

        held = (await client.get(f'/clients/{service.client_id}/extension')).json()

        assert held['sha256'] == hashlib.sha256(bundle.archive).hexdigest()

    def test_the_upload_route_has_a_larger_limit_than_everything_else(self):
        """The general cap is 32 MB; a wheelhouse for three platforms is
        bigger, so the upload route has its own — and only that route."""
        app = the_app()
        configured = next(m for m in app.user_middleware if m.cls is BodySizeLimitMiddleware)
        limiter = BodySizeLimitMiddleware(app, **configured.kwargs)

        assert limiter._limit_for('/v1/clients/abc/extension') == MAX_ARCHIVE_BYTES
        assert limiter._limit_for('/v1/chat/completions') == settings.max_request_body_bytes
        assert limiter._limit_for('/v1/clients') == settings.max_request_body_bytes

    async def test_a_pending_service_may_upload_so_the_admin_can_see_it(self, client):
        service = RegisteredService()
        await service.approve(state='pending')

        assert (await _upload(client, service, _bundle())).status_code == 200

    async def test_a_revoked_service_may_not(self, client):
        service = RegisteredService()
        await service.approve(state='revoked')

        assert (await _upload(client, service, _bundle())).status_code == 400

    async def test_signed_by_another_key(self, client):
        """The registered key is public; anybody can name the client. Only
        the client can sign."""
        service = RegisteredService()
        await service.approve()
        impostor = RegisteredService()
        impostor.client_id = service.client_id
        impostor.public = service.public.model_copy()  # names the real kid
        bundle = _bundle()

        response = await _upload(
            client, service, bundle, bundle.signed_by(impostor, extension='igni.codeindex', version='1.2.0')
        )

        assert response.status_code == 400
        assert 'registered key' in response.json()['detail']

    async def test_signed_for_other_bytes(self, client):
        service = RegisteredService()
        await service.approve()
        bundle = _bundle()

        response = await _upload(
            client,
            service,
            bundle,
            bundle.signed_by(service, extension='igni.codeindex', version='1.2.0', sha256='0' * 64),
        )

        assert 'different bytes' in response.json()['detail']

    async def test_signed_for_another_version(self, client):
        service = RegisteredService()
        await service.approve()
        bundle = _bundle()

        response = await _upload(
            client, service, bundle, bundle.signed_by(service, extension='igni.codeindex', version='9.9.9')
        )

        assert 'different extension or version' in response.json()['detail']

    async def test_not_a_bundle(self, client):
        service = RegisteredService()
        await service.approve()
        junk = ExtensionBundle({})
        junk.archive = b'not a tarball'

        response = await _upload(
            client, service, junk, junk.signed_by(service, extension='igni.codeindex', version='1.2.0')
        )

        assert response.status_code == 400

    async def test_a_tool_that_leaves_a_hint_unstated(self, client):
        """igni's permission modes decide on all four; a default is a claim nobody made."""
        service = RegisteredService()
        await service.approve()
        unstated = {
            'workspace': 'read',
            'tools': [{'name': 'igni.codeindex.search', 'annotations': {'readOnlyHint': True}}],
        }

        response = await _upload(client, service, _bundle(permissions=unstated))

        assert response.status_code == 400
        assert 'destructiveHint' in response.json()['detail']

    async def test_a_tool_named_with_no_hints(self, client):
        service = RegisteredService()
        await service.approve()

        response = await _upload(
            client, service, _bundle(permissions={'workspace': 'read', 'tools': ['igni.codeindex.search']})
        )

        assert response.status_code == 400

    async def test_no_manifest(self, client):
        service = RegisteredService()
        await service.approve()
        bundle = ExtensionBundle({'README.md': 'hi'})

        response = await _upload(client, service, bundle)

        assert 'no manifest.json' in response.json()['detail']

    async def test_a_path_outside_the_bundle(self, client):
        service = RegisteredService()
        await service.approve()
        bundle = ExtensionBundle({'manifest.json': '{}', '../escape.md': 'x'})

        response = await _upload(client, service, bundle)

        assert response.status_code == 400

    async def test_a_new_version_replaces_the_old(self, client, db):
        service = RegisteredService()
        await service.approve()
        await _upload(client, service, _bundle())
        newer = _bundle(version='1.3.0')

        await _upload(client, service, newer, newer.signed_by(service, extension='igni.codeindex', version='1.3.0'))

        admin = await _super_admin(db)
        detail = (await client.get(f'/portal/clients/{service.client_id}', headers=admin)).json()
        assert detail['extension']['version'] == '1.3.0'


class TestTheAdminSeesWhatTheyApprove:
    async def test_the_whole_extension_is_described(self, client, db):
        service = RegisteredService()
        await service.approve(state='pending')
        await _upload(client, service, _bundle())

        detail = (await client.get(f'/portal/clients/{service.client_id}', headers=await _super_admin(db))).json()

        extension = detail['extension_detail']
        assert extension['name'] == 'CodeIndex'
        assert extension['manifest']['contributes']['commands'] == [
            {'command': '/codeindex', 'desc': 'semantic code search'}
        ]
        assert extension['manifest']['permissions']['workspace'] == 'read'
        assert [a['name'] for a in extension['plugin']['agents']] == ['index-helper']
        assert extension['plugin']['agent_overrides'] == [
            {
                'agent': 'explorer',
                'file': 'plugin/overrides/explorer.md',
                'description': 'Explores with the index',
                'exclusive': False,
                'when': None,
            }
        ]
        assert [o['target'] for o in extension['review']['impact']['prompt_overrides']] == ['agent:explorer']
        assert extension['review']['collisions'] == []
        assert extension['plugin']['skills'] == [{'name': 'reindex', 'description': 'Rebuild the index'}]

    async def test_the_list_says_which_extension_each_ships(self, client, db):
        service = RegisteredService()
        await service.approve(state='pending')
        await _upload(client, service, _bundle())

        listed = (await client.get('/portal/clients', headers=await _super_admin(db))).json()

        assert listed[0]['extension']['extension_id'] == 'igni.codeindex'


class TestDistribution:
    async def _person(self, db) -> dict[str, str]:
        return await EntitledPerson(db).headers('igni.codeindex')

    async def test_an_approved_services_extension_reaches_machines(self, client, db):
        service = RegisteredService()
        await service.approve()
        bundle = _bundle()
        await _upload(client, service, bundle)

        listed = (await client.get('/extensions', headers=await self._person(db))).json()['extensions']

        assert len(listed) == 1
        entry = listed[0]
        assert entry['extension_id'] == 'igni.codeindex'
        assert entry['service_url'] == 'https://codeindex.example.com'
        assert entry['publisher_jwks']['keys'][0]['kid'] == service.public.kid
        assert entry['sha256'] == hashlib.sha256(bundle.archive).hexdigest()

    async def test_the_bundle_comes_with_its_signature(self, client, db):
        service = RegisteredService()
        await service.approve()
        bundle = _bundle()
        await _upload(client, service, bundle)

        response = await client.get('/extensions/igni.codeindex/bundle', headers=await self._person(db))

        assert response.content == bundle.archive
        assert response.headers['x-igni-bundle-signature']
        assert response.headers['content-type'] == 'application/gzip'

    async def test_nothing_from_a_pending_service(self, client, db):
        service = RegisteredService()
        await service.approve(state='pending')
        await _upload(client, service, _bundle())
        headers = await self._person(db)

        assert (await client.get('/extensions', headers=headers)).json()['extensions'] == []
        assert (await client.get('/extensions/igni.codeindex/bundle', headers=headers)).status_code == 404

    async def test_only_for_somebody_signed_in(self, client):
        assert (await client.get('/extensions')).status_code == 401


class TestOneIdOneService:
    async def test_a_second_service_cannot_ship_an_approved_id(self, client):
        first = RegisteredService()
        await first.approve()
        await _upload(client, first, _bundle())
        second = RegisteredService(name='Imposter', base_url='https://other.example.com')
        await second.approve()

        response = await _upload(client, second, _bundle())

        assert response.status_code == 409

    async def test_approving_a_second_one_is_refused(self, client, db):
        """Both uploaded while pending, which nothing can refuse yet; the
        second approval is where the clash is caught."""
        admin = await _super_admin(db)
        first = RegisteredService()
        await first.approve(state='pending')
        await _upload(client, first, _bundle())
        second = RegisteredService(name='Imposter', base_url='https://other.example.com')
        await second.approve(state='pending')
        await _upload(client, second, _bundle())
        await client.post(f'/portal/clients/{first.client_id}/approve', headers=admin)

        response = await client.post(f'/portal/clients/{second.client_id}/approve', headers=admin)

        assert response.status_code == 409
