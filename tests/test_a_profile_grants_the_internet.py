"""Internet access is granted per VM profile, not per node.

A VM reaches only what its node allows (`[[network.allow]]`) unless its
profile says `internet: true`. An org admin sets it on a profile, or
approves it when an app or an extension asks for it; the request shows
it, the audit trail records it, and the producers feed carries it to the
gateway (`docs/service-identity.md` §12.4). Every profile that existed
before t0026 is internal-only, and adding the field moves no profile's
version: the gateway would re-provision every VM. A request's version
covers the field whatever its value; t0028 stored every request kept
before at that version, so none goes back to pending.
"""

from __future__ import annotations

import json
import uuid

from alembic.config import Config
import pytest
from sqlalchemy import create_engine, Engine, text

from alembic import command

from app.api.base_v1.schema.effective_vm_profile import EffectiveVmProfile
from app.api.base_v1.schema.vm_profile_requests import ManifestVm, VmProfileRequestSettings
from app.api.base_v1.schema.vm_profiles import VmProfileSettings
from app.services.cloud.vm_profile_request_records import VmProfileRequestRecords
from app.services.cloud.vm_profile_versions import ProfileVersion
from tests.cloud_world import CloudOrg, platform, shipped
from tests.conftest import audit_rows, TEST_DB_URL_SYNC

WEB = {
    'memory_mib': 4096,
    'vcpus': 2,
    'packages': {'apt': ['chromium']},
    'internet': True,
}


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


def _profiles(acme: CloudOrg) -> str:
    return f'/portal/orgs/{acme.org_id}/vm-profiles'


def _requests(acme: CloudOrg) -> str:
    return f'/portal/orgs/{acme.org_id}/vm-profile-requests'


async def _served(client, gateway, kind: str, producer_id: str) -> dict:
    response = await client.get('/platform/producers', params={'limit': 1000}, headers=gateway)
    assert response.status_code == 200, response.text
    rows = [row for row in response.json()['producers'] if row['producer'] == {'kind': kind, 'id': producer_id}]
    return dict(rows[-1])


class TestTheSchema:
    def test_a_profile_is_internal_only_unless_it_says_otherwise(self):
        assert VmProfileSettings(name='Standard').internet is False
        assert VmProfileSettings(name='Web', internet=True).internet is True

    def test_a_request_and_a_manifest_may_ask_for_it(self):
        assert VmProfileRequestSettings().internet is False
        assert VmProfileRequestSettings(internet=True).internet is True
        assert ManifestVm.model_validate({'internet': True}).settings().internet is True

    def test_a_feed_row_written_before_it_existed_reads_as_internal_only(self):
        before = {
            'profile_id': 'p',
            'profile_name': 'Standard',
            'memory_mib': 4096,
            'normal_memory_mib': 1024,
            'expected_memory_mib': None,
            'vcpus': 1,
            'idle_before_pause_s': 10,
            'packages': {'apt': [], 'pip': [], 'npm': []},
            'setup_script': None,
            'volume_max_gib': 50,
        }

        assert EffectiveVmProfile.model_validate(before).internet is False


class TestNoVersionMoves:
    def test_an_internal_only_profile_keeps_the_version_it_had(self):
        settings = VmProfileSettings(name='Standard')
        before = settings.model_dump(mode='json', exclude={'internet', 'erase_after_idle_days'})

        assert ProfileVersion.of('p', settings) == ProfileVersion.digest({'id': 'p', **before})
        assert ProfileVersion.of('p', settings.model_copy(update={'internet': True})) != ProfileVersion.of(
            'p', settings
        )

    def test_a_request_version_covers_everything_asked_for(self):
        asked = VmProfileRequestSettings(vcpus=2)

        assert VmProfileRequestRecords.version_of(asked) == ProfileVersion.digest(
            {'requested': asked.model_dump(mode='json')}
        )
        assert VmProfileRequestRecords.version_of(asked.model_copy(update={'internet': True})) != (
            VmProfileRequestRecords.version_of(asked)
        )


@pytest.mark.anyio
class TestAnOrgAdminGrantsIt:
    async def test_on_a_profile_and_the_feed_carries_it(self, client, acme):
        app = await acme.app()
        gateway = await platform(client)
        before = await _served(client, gateway, 'app', app.client_id)

        made = await client.post(_profiles(acme), json={'name': 'Web', **WEB}, headers=acme.admin)
        assert made.status_code == 201, made.text
        await client.put(
            f'/portal/orgs/{acme.org_id}/apps/{app.client_id}/vm-profile',
            json={'profile_id': made.json()['id']},
            headers=acme.admin,
        )
        after = await _served(client, gateway, 'app', app.client_id)

        assert before['profile']['internet'] is False
        assert made.json()['internet'] is True
        assert after['profile']['internet'] is True
        assert after['profile_version'] != before['profile_version']
        overview = (await client.get(_profiles(acme), headers=acme.admin)).json()
        assert {p['name']: p['internet'] for p in overview['profiles']} == {'Standard': False, 'Web': True}

    async def test_taking_it_away_reaches_the_feed(self, client, acme):
        app = await acme.app()
        gateway = await platform(client)
        made = (await client.post(_profiles(acme), json={'name': 'Web', **WEB}, headers=acme.admin)).json()
        await client.put(
            f'/portal/orgs/{acme.org_id}/apps/{app.client_id}/vm-profile',
            json={'profile_id': made['id']},
            headers=acme.admin,
        )

        await client.put(
            f'{_profiles(acme)}/{made["id"]}', json={'name': 'Web', **WEB, 'internet': False}, headers=acme.admin
        )

        assert (await _served(client, gateway, 'app', app.client_id))['profile']['internet'] is False

    async def test_the_audit_trail_records_the_grant(self, client, acme):
        made = (await client.post(_profiles(acme), json={'name': 'Web', **WEB}, headers=acme.admin)).json()
        await client.put(
            f'{_profiles(acme)}/{made["id"]}', json={'name': 'Web', **WEB, 'internet': False}, headers=acme.admin
        )

        rows = [r for r in audit_rows(target_type='vm_profile') if r['target_id'] == made['id']]

        assert sorted((r['method'], r['detail']['internet']) for r in rows) == [('POST', True), ('PUT', False)]


@pytest.mark.anyio
class TestAnAppAsksForIt:
    async def _bot(self, acme: CloudOrg) -> tuple[str, dict[str, str]]:
        app = await acme.app('Web researcher')
        return app.client_id, await acme.app_token(app)

    async def test_pending_changes_nothing_and_approving_grants_it(self, client, acme):
        app_id, token = await self._bot(acme)
        gateway = await platform(client)

        asked = await client.put(f'/apps/{app_id}/vm-profile-request', json=WEB, headers=token)
        assert asked.status_code == 201, asked.text
        pending = await _served(client, gateway, 'app', app_id)
        [listed] = (await client.get(_requests(acme), headers=acme.admin)).json()['requests']
        approved = await client.post(
            f'{_requests(acme)}/{listed["id"]}/approve', json={'version': listed['version']}, headers=acme.admin
        )
        assert approved.status_code == 200, approved.text
        granted = await _served(client, gateway, 'app', app_id)

        assert asked.json()['requested']['internet'] is True
        assert listed['requested']['internet'] is True, 'the approving admin sees what is asked'
        assert pending['profile']['internet'] is False
        assert granted['profile']['internet'] is True
        own = (await client.get(f'/apps/{app_id}/vm-profile', headers=token)).json()
        assert own['internet'] is True

    async def test_asking_and_approving_record_it(self, client, acme):
        app_id, token = await self._bot(acme)
        asked = (await client.put(f'/apps/{app_id}/vm-profile-request', json=WEB, headers=token)).json()
        await client.post(
            f'{_requests(acme)}/{asked["id"]}/approve', json={'version': asked['version']}, headers=acme.admin
        )

        rows = [r for r in audit_rows(target_type='vm_profile_request') if r['target_id'] == asked['id']]
        by_state = {(r['method'], r['detail'].get('state')): r['detail'] for r in rows}

        assert by_state[('PUT', 'pending')]['internet'] is True
        assert by_state[('POST', 'approved')]['internet'] is True

    async def test_asking_for_it_later_is_a_new_request(self, client, acme):
        app_id, token = await self._bot(acme)
        internal = {**WEB, 'internet': False}
        first = (await client.put(f'/apps/{app_id}/vm-profile-request', json=internal, headers=token)).json()
        await client.post(
            f'{_requests(acme)}/{first["id"]}/approve', json={'version': first['version']}, headers=acme.admin
        )

        again = await client.put(f'/apps/{app_id}/vm-profile-request', json=internal, headers=token)
        more = await client.put(f'/apps/{app_id}/vm-profile-request', json=WEB, headers=token)

        assert (again.status_code, again.json()['state']) == (200, 'approved')
        assert (more.status_code, more.json()['state']) == (201, 'pending')


@pytest.mark.anyio
class TestAnExtensionAsksForIt:
    async def test_through_its_manifest(self, client, acme):
        await shipped(client, 'acme.web', vm={'memoryMib': 4096, 'internet': True})

        [request] = (await client.get(_requests(acme), headers=acme.admin)).json()['requests']

        assert request['requested']['internet'] is True


_SCRATCH = f'igni_internet_{uuid.uuid4().hex[:8]}'


def _url(database: str) -> str:
    base, _, _ = TEST_DB_URL_SYNC.rpartition('/')
    return f'{base}/{database}'


@pytest.fixture(scope='module')
def tenant():
    """A tenant database of this module's own, built from `alembic_tenant/` alone."""
    admin = create_engine(_url('postgres'), isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE {_SCRATCH}'))
    except Exception as exc:  # pragma: no cover - environment dependent
        admin.dispose()
        pytest.skip(f'cannot create a scratch database here: {type(exc).__name__}: {exc}')
    cfg = Config()
    cfg.set_main_option('script_location', 'alembic_tenant')
    cfg.set_main_option('sqlalchemy.url', _url(_SCRATCH))
    engine = create_engine(_url(_SCRATCH))
    try:
        yield cfg, engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(
                text(
                    'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                    f"WHERE datname = '{_SCRATCH}' AND pid <> pg_backend_pid()"
                )
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS {_SCRATCH}'))
        admin.dispose()


def _columns(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'vm_profiles'")
            ).scalars()
        )


class TestTheMigration:
    def test_t0026_makes_every_existing_profile_internal_only_and_rolls_back(self, tenant):
        cfg, engine = tenant
        command.upgrade(cfg, 't0025')
        with engine.begin() as conn:
            conn.execute(
                text(
                    'INSERT INTO vm_profiles (id, name, is_default, memory_mib, vcpus, packages) '
                    "VALUES ('p-1', 'Browser', false, 4096, 2, '{}')"
                )
            )

        command.upgrade(cfg, 't0026')
        with engine.connect() as conn:
            kept = conn.execute(text("SELECT internet FROM vm_profiles WHERE id = 'p-1'")).scalar_one()
        command.downgrade(cfg, 't0025')
        rolled_back = _columns(engine)
        command.upgrade(cfg, 'head')

        assert kept is False
        assert 'internet' not in rolled_back
        assert 'internet' in _columns(engine)

    def test_t0028_gives_every_request_the_version_it_reads_with_now_and_rolls_back(self, tenant):
        cfg, engine = tenant
        command.upgrade(cfg, 'head')
        command.downgrade(cfg, 't0027')
        kept = VmProfileRequestSettings(vcpus=2)
        before = kept.model_dump(mode='json', exclude={'internet'})
        old_version = ProfileVersion.digest({'requested': before})
        with engine.begin() as conn:
            conn.execute(
                text(
                    'INSERT INTO vm_profile_requests (id, subject_kind, subject_id, settings, version, state, '
                    'requested_by_kind, requested_by_id, requested_at) '
                    "VALUES ('r-1', 'app', 'a-1', CAST(:settings AS JSONB), :version, 'approved', 'app', 'a-1', now())"
                ),
                {'settings': json.dumps(before), 'version': old_version},
            )

        command.upgrade(cfg, 't0028')
        with engine.connect() as conn:
            settings, version = conn.execute(
                text("SELECT settings, version FROM vm_profile_requests WHERE id = 'r-1'")
            ).one()
        command.downgrade(cfg, 't0027')
        with engine.connect() as conn:
            rolled_back = conn.execute(text("SELECT version FROM vm_profile_requests WHERE id = 'r-1'")).scalar_one()
        command.upgrade(cfg, 'head')

        assert settings['internet'] is False
        assert version == VmProfileRequestRecords.version_of(kept)
        assert rolled_back == old_version
