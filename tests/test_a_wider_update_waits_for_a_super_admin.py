"""An approved service cannot widen its extension without a super admin.

Approval covers what the super admin read. So once a service is
approved, a new version that asks for the same or less goes live at
once; one that asks for more — a host, a path, a secret, a tool, a hook,
a prompt section (`tests/test_an_extensions_grants_are_compared.py`) —
is **held**: the upload succeeds and says so, machines keep receiving
the approved version, and the super admin approves or rejects it from
the Services page or the API, recorded in the audit trail.
"""

from __future__ import annotations

import hashlib

import pytest

from tests.conftest import audit_rows, auth_header, create_user, make_token
from tests.entitled_person import EntitledPerson
from tests.registered_service import ExtensionAssetFile, ExtensionBundle, RegisteredService

pytestmark = pytest.mark.anyio

READS = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}


def _manifest(version: str, *, egress: list[str] | None = None, **extra: object) -> dict[str, object]:
    return {
        'id': 'igni.codeindex',
        'version': version,
        'name': 'CodeIndex',
        'permissions': {
            'workspace': 'read',
            'egress': ['api.codeindex.example'] if egress is None else egress,
            'tools': [{'name': 'igni.codeindex.search', 'annotations': READS}],
        },
        **extra,
    }


WIDER = {'egress': ['api.codeindex.example', 'telemetry.example']}
NARROWER = {'egress': []}


async def _upload(client, service: RegisteredService, version: str, **manifest: object):
    bundle = ExtensionBundle.of(_manifest(version, **manifest))
    response = await client.put(
        f'/clients/{service.client_id}/extension',
        content=bundle.archive,
        headers={
            'Content-Type': 'application/gzip',
            'X-Igni-Bundle-Signature': bundle.signed_by(service, extension='igni.codeindex', version=version),
        },
    )
    assert response.status_code == 200, response.text
    return response.json(), bundle


async def _approved_with_one_version(client) -> RegisteredService:
    service = RegisteredService()
    await service.approve()
    await _upload(client, service, '1.0.0')
    return service


async def _super_admin(db) -> dict[str, str]:
    user = await create_user(db, email='owner@example.com', is_super_admin=True)
    return auth_header(make_token(str(user.user_id)))


async def _distributed(client, headers) -> list[str]:
    listed = (await client.get('/extensions', headers=headers)).json()['extensions']
    return [entry['version'] for entry in listed]


class TestOnUpload:
    async def test_a_narrower_update_goes_live(self, client, db):
        service = await _approved_with_one_version(client)
        person = await EntitledPerson(db).headers('igni.codeindex')

        published, bundle = await _upload(client, service, '1.1.0', **NARROWER)

        assert published['held'] is False
        assert await _distributed(client, person) == ['1.1.0']
        fetched = await client.get('/extensions/igni.codeindex/bundle', headers=person)
        assert fetched.content == bundle.archive

    async def test_the_same_grants_go_live(self, client, db):
        service = await _approved_with_one_version(client)
        person = await EntitledPerson(db).headers('igni.codeindex')

        published, _ = await _upload(client, service, '1.0.1')

        assert published['held'] is False
        assert await _distributed(client, person) == ['1.0.1']

    async def test_a_wider_update_is_held_and_not_distributed(self, client, db):
        service = await _approved_with_one_version(client)
        person = await EntitledPerson(db).headers('igni.codeindex')

        published, bundle = await _upload(client, service, '2.0.0', **WIDER)

        assert published['held'] is True
        assert published['version'] == '2.0.0'
        assert 'telemetry.example' in published['reason']
        assert [(g['kind'], g['item']) for g in published['added']] == [('egress', 'telemetry.example')]
        assert await _distributed(client, person) == ['1.0.0']
        fetched = await client.get('/extensions/igni.codeindex/bundle', headers=person)
        assert fetched.content != bundle.archive
        assert fetched.status_code == 200

    async def test_the_service_is_told_it_holds_the_held_bytes(self, client):
        """So a restart does not upload the same version again."""
        service = await _approved_with_one_version(client)
        _, bundle = await _upload(client, service, '2.0.0', **WIDER)

        held = (await client.get(f'/clients/{service.client_id}/extension')).json()

        assert held['sha256'] == hashlib.sha256(bundle.archive).hexdigest()
        assert held['held'] is True

    async def test_a_newer_upload_replaces_the_held_one(self, client, db):
        service = await _approved_with_one_version(client)
        admin = await _super_admin(db)
        await _upload(client, service, '2.0.0', **WIDER)

        await _upload(client, service, '2.0.1', egress=['api.codeindex.example', 'other.example'])

        updates = (await client.get('/portal/held-updates', headers=admin)).json()
        assert [(u['version'], u['approved_version']) for u in updates] == [('2.0.1', '1.0.0')]
        assert [g['item'] for g in updates[0]['comparison']['added']] == ['other.example']

    async def test_a_newer_narrower_upload_goes_live_and_drops_the_held_one(self, client, db):
        service = await _approved_with_one_version(client)
        admin = await _super_admin(db)
        person = await EntitledPerson(db).headers('igni.codeindex')
        await _upload(client, service, '2.0.0', **WIDER)

        published, _ = await _upload(client, service, '1.0.2', **NARROWER)

        assert published['held'] is False
        assert (await client.get('/portal/held-updates', headers=admin)).json() == []
        assert await _distributed(client, person) == ['1.0.2']

    async def test_a_pending_services_upload_is_never_held(self, client, db):
        """Its approval is the review of what it uploaded last."""
        service = RegisteredService()
        await service.approve(state='pending')
        await _upload(client, service, '1.0.0', **NARROWER)

        published, _ = await _upload(client, service, '2.0.0', **WIDER)

        assert published['held'] is False

    async def test_an_asset_only_the_held_version_declares_is_kept(self, client, db):
        service = await _approved_with_one_version(client)
        model = ExtensionAssetFile('model', 'any', b'a model only 2.0 needs')
        await client.put(
            f'/clients/{service.client_id}/extension/assets/{model.sha256}',
            content=model.content,
            headers={'Content-Type': 'application/octet-stream', 'X-Igni-Asset-Signature': model.signed_by(service)},
        )

        published, _ = await _upload(client, service, '2.0.0', assets=[model.declaration], **WIDER)

        assert published['held'] is True
        assert (await client.head(f'/clients/{service.client_id}/extension/assets/{model.sha256}')).status_code == 200


class TestTheSuperAdminDecides:
    async def test_the_services_page_shows_what_it_asks_for(self, client, db):
        service = await _approved_with_one_version(client)
        admin = await _super_admin(db)
        await _upload(client, service, '2.0.0', **WIDER)

        listed = (await client.get('/portal/clients', headers=admin)).json()
        detail = (await client.get(f'/portal/clients/{service.client_id}', headers=admin)).json()

        assert listed[0]['held_version'] == '2.0.0'
        assert listed[0]['extension']['version'] == '1.0.0'
        assert detail['extension_detail']['version'] == '1.0.0'
        held = detail['held_update']
        assert held['version'] == '2.0.0'
        assert held['approved_version'] == '1.0.0'
        assert held['service'] == 'CodeIndex'
        assert held['comparison']['added'] == [
            {'kind': 'egress', 'label': 'Network egress', 'item': 'telemetry.example', 'was': None}
        ]

    async def test_approving_makes_it_the_distributed_version(self, client, db):
        service = await _approved_with_one_version(client)
        admin = await _super_admin(db)
        person = await EntitledPerson(db).headers('igni.codeindex')
        _, bundle = await _upload(client, service, '2.0.0', **WIDER)

        response = await client.post(f'/portal/clients/{service.client_id}/held-update/approve', headers=admin)

        assert response.status_code == 200, response.text
        assert response.json()['extension']['version'] == '2.0.0'
        assert response.json()['held_version'] is None
        assert await _distributed(client, person) == ['2.0.0']
        fetched = await client.get('/extensions/igni.codeindex/bundle', headers=person)
        assert fetched.content == bundle.archive
        assert (await client.get('/portal/held-updates', headers=admin)).json() == []

    async def test_once_approved_the_same_grants_go_live_again(self, client, db):
        service = await _approved_with_one_version(client)
        admin = await _super_admin(db)
        await _upload(client, service, '2.0.0', **WIDER)
        await client.post(f'/portal/clients/{service.client_id}/held-update/approve', headers=admin)

        published, _ = await _upload(client, service, '2.0.1', **WIDER)

        assert published['held'] is False

    async def test_rejecting_discards_it_and_keeps_the_approved_version(self, client, db):
        service = await _approved_with_one_version(client)
        admin = await _super_admin(db)
        person = await EntitledPerson(db).headers('igni.codeindex')
        await _upload(client, service, '2.0.0', **WIDER)

        response = await client.post(f'/portal/clients/{service.client_id}/held-update/reject', headers=admin)

        assert response.status_code == 200, response.text
        assert response.json()['extension']['version'] == '1.0.0'
        assert response.json()['held_version'] is None
        assert await _distributed(client, person) == ['1.0.0']
        assert (await client.get('/portal/held-updates', headers=admin)).json() == []
        published = (await client.get(f'/clients/{service.client_id}/extension')).json()
        assert (published['version'], published['held']) == ('1.0.0', False)

    async def test_nothing_held_is_a_404(self, client, db):
        service = await _approved_with_one_version(client)
        admin = await _super_admin(db)

        for decision in ('approve', 'reject'):
            response = await client.post(f'/portal/clients/{service.client_id}/held-update/{decision}', headers=admin)
            assert response.status_code == 404

    async def test_only_a_super_admin(self, client, db):
        service = await _approved_with_one_version(client)
        await _upload(client, service, '2.0.0', **WIDER)
        member = await create_user(db, email='member@example.com')
        headers = auth_header(make_token(str(member.user_id)))

        assert (await client.get('/portal/held-updates', headers=headers)).status_code == 403
        response = await client.post(f'/portal/clients/{service.client_id}/held-update/approve', headers=headers)
        assert response.status_code == 403

    @pytest.mark.parametrize('action,decision', [('approve', 'approved'), ('reject', 'rejected')])
    async def test_the_decision_is_audited_with_what_it_added(self, client, db, action, decision):
        service = await _approved_with_one_version(client)
        admin = await _super_admin(db)
        await _upload(client, service, '2.0.0', **WIDER)

        await client.post(f'/portal/clients/{service.client_id}/held-update/{action}', headers=admin)

        rows = [r for r in audit_rows(target_id=service.client_id) if r['route'].endswith(f'/held-update/{action}')]
        assert len(rows) == 1, audit_rows(target_id=service.client_id)
        detail = rows[0]['detail']
        assert detail['decision'] == f'held update {decision}'
        assert detail['version'] == '2.0.0'
        assert detail['approved_version'] == '1.0.0'
        assert detail['added'] == ['Network egress: telemetry.example']
        assert rows[0]['actor_user_id'] is not None
