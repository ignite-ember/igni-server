"""The gateway's feed: every producer that appears, goes, or gets another profile.

`GET /platform/producers?since=&limit=` lists people and apps whose
producer-relevant state changed after the cursor, oldest change first, so
the gateway provisions a VM ahead of its producer's first request and
again after a profile change (`docs/service-identity.md` §11.3; the
gateway's side is igni__cloudvm's `docs/gateway.md`).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import update

from app.api.base_v1.schema.effective_vm_profile import EffectiveVmProfile
from app.config import settings
from app.db.sql.models.producer_feed import ProducerFeedEntry
from app.db.sql.producer_feed import ProducerFeedDB
from app.security.producer import ProducerRef
from app.services import retention
from app.services.cloud.producer_feed import ProducerFeed
from app.services.cloud.producer_states import ProducerStates
from app.services.cloud.vm_profiles import VmProfiles
from tests.cloud_world import CloudOrg, make_super_admin, platform, shared_plane
from tests.conftest import add_member

pytestmark = pytest.mark.anyio

BROWSER = {
    'name': 'Browser',
    'memory_mib': 4096,
    'vcpus': 2,
    'idle_before_pause_s': 600,
    'packages': {'apt': ['chromium'], 'pip': [], 'npm': []},
    'setup_script': None,
}


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    """Enabling the cloud for an app keeps its credential encrypted until it is picked up."""
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


@pytest.fixture
async def gateway(client) -> dict[str, str]:
    return await platform(client)


class Feed:
    """The gateway's side: a cursor carried from one poll to the next."""

    def __init__(self, client, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = headers
        self.cursor: str | None = None

    async def poll(self, limit: int | None = None) -> list[dict]:
        params: dict[str, str | int] = {}
        if self.cursor is not None:
            params['since'] = self.cursor
        if limit is not None:
            params['limit'] = limit
        response = await self.client.get('/platform/producers', params=params, headers=self.headers)
        assert response.status_code == 200, response.text
        body = response.json()
        self.cursor = body['cursor']
        return list(body['producers'])


class WorkedOut:
    """What igni server works out for one producer apart from the feed: what its row must carry.

    The profile and volume from `VmProfiles.for_producer` (which the
    volume routes use), the versions from the profiles page.
    """

    def __init__(self, client, acme: CloudOrg) -> None:
        self.client = client
        self.acme = acme

    async def of(self, kind: str, producer_id: str) -> dict:
        served = await VmProfiles().for_producer(self.acme.org_id, ProducerRef(kind=kind, id=producer_id))
        overview = await self.client.get(f'/portal/orgs/{self.acme.org_id}/vm-profiles', headers=self.acme.admin)
        [listed] = [p for p in overview.json()['profiles'] if p['id'] == served.profile_id]
        return {
            'profile': EffectiveVmProfile.model_validate(served.model_dump()).model_dump(),
            'volume_gib': served.volume_gib,
            'profile_version': listed['version'],
            'packages_version': listed['packages_version'],
        }


def _who(rows: list[dict]) -> list[tuple[str, str, bool]]:
    return [(row['producer']['kind'], row['producer']['id'], row['active']) for row in rows]


def _fate(rows: list[dict]) -> list[tuple[str, bool, bool]]:
    """Each row's producer, whether it is active, and whether it was removed."""
    return sorted((row['producer']['id'], row['active'], row['removed']) for row in rows)


async def test_only_a_platform_token_reads_it(client, acme):
    assert (await client.get('/platform/producers', headers=acme.admin)).status_code == 401
    assert (await client.get('/platform/producers')).status_code == 401


async def test_the_first_poll_lists_every_producer_with_its_profile(client, acme, gateway):
    feed = Feed(client, gateway)
    ada, _ = await acme.person('ada@acme.com')
    app = await acme.app()

    rows = await feed.poll()

    assert sorted(_who(rows)) == sorted(
        [('person', acme.admin_id, True), ('person', ada, True), ('app', app.client_id, False)]
    )
    standard = await WorkedOut(client, acme).of('person', ada)
    row = next(r for r in rows if r['producer']['id'] == ada)
    assert row == {
        'org_id': acme.org_id,
        'producer': {'kind': 'person', 'id': ada},
        'incarnation': row['incarnation'],
        'active': True,
        'removed': False,
        'profile_version': standard['profile_version'],
        'packages_version': standard['packages_version'],
        'profile': standard['profile'],
        'volume_gib': standard['volume_gib'],
        'erase_at': None,
        'owner': None,
        'changed_at': row['changed_at'],
    }
    assert await feed.poll() == []


async def test_a_new_member_appears_once(client, acme, gateway):
    feed = Feed(client, gateway)
    await feed.poll()

    ada, _ = await acme.person('ada@acme.com')

    assert _who(await feed.poll()) == [('person', ada, True)]
    assert await feed.poll() == []


async def test_deactivation_only_suspends_a_person(client, acme, gateway):
    """Inactive, not removed: the VM is suspended, its volume and keys kept."""
    feed = Feed(client, gateway)
    ada, _ = await acme.person('ada@acme.com')
    await feed.poll()

    deactivated = await client.patch(f'/portal/team/{ada}/status', json={'status': 'deactivated'}, headers=acme.admin)
    suspended = await feed.poll()
    await client.patch(f'/portal/team/{ada}/status', json={'status': 'active'}, headers=acme.admin)
    back = await feed.poll()

    assert deactivated.status_code == 200, deactivated.text
    assert _fate(suspended) == [(ada, False, False)]
    assert _fate(back) == [(ada, True, False)]
    assert suspended[0]['incarnation'] == back[0]['incarnation']


async def test_removal_from_the_organisation_removes_the_person_there(client, acme, gateway):
    """The organisation owns that VM's data: removing the member erases it."""
    feed = Feed(client, gateway)
    ada, _ = await acme.person('ada@acme.com')
    await feed.poll()

    removed = await client.delete(f'/portal/team/{ada}', headers=acme.admin)

    assert removed.status_code in (200, 204), removed.text
    assert _fate(await feed.poll()) == [(ada, False, True)]


async def test_each_row_carries_the_profile_its_vm_gets_until_it_is_removed(client, acme, gateway):
    """The gateway wakes a VM from its row alone: a person gets their group's, an app its own, a space its app's."""
    feed = Feed(client, gateway)
    browser = (await client.post(f'/portal/orgs/{acme.org_id}/vm-profiles', json=BROWSER, headers=acme.admin)).json()
    legal = await acme.group('Legal')
    await client.put(
        f'/portal/orgs/{acme.org_id}/groups/{legal}/vm-profile', json={'profile_id': browser['id']}, headers=acme.admin
    )
    ada, _ = await acme.person('ada@acme.com', legal)
    app = await acme.app()
    declared = await client.put(f'/apps/{app.client_id}/spaces/cust-1', headers=await acme.app_token(app))
    space = declared.json()['space_id']
    rows = {(r['producer']['kind'], r['producer']['id']): r for r in await feed.poll()}

    for kind, producer_id in [('person', acme.admin_id), ('person', ada), ('app', app.client_id), ('space', space)]:
        served = await WorkedOut(client, acme).of(kind, producer_id)
        assert rows[(kind, producer_id)]['profile'] == served['profile'], (kind, producer_id)
    assert rows[('person', ada)]['profile']['profile_name'] == 'Browser'
    assert rows[('person', acme.admin_id)]['profile']['profile_name'] == 'Standard'
    assert rows[('space', space)]['profile'] == rows[('app', app.client_id)]['profile']

    await client.delete(f'/portal/team/{ada}', headers=acme.admin)
    [gone] = await feed.poll()

    assert (gone['producer']['id'], gone['removed'], gone['profile']) == (ada, True, None)


async def test_a_row_written_before_the_feed_carried_profiles_is_listed_again_with_one(client, acme, gateway):
    feed = Feed(client, gateway)
    ada, _ = await acme.person('ada@acme.com')
    [before] = [r for r in await feed.poll() if r['producer']['id'] == ada]
    async with shared_plane() as db:
        await db.execute(update(ProducerFeedEntry).where(ProducerFeedEntry.producer_id == ada).values(vm_profile=None))
        await db.commit()

    await ProducerFeed().reconcile()
    [again] = await feed.poll()

    assert again['producer']['id'] == ada
    assert again['profile'] == before['profile'] is not None
    assert again['profile_version'] == before['profile_version']


async def test_added_again_a_person_is_a_new_incarnation_and_the_old_one_stays_removed(client, acme, gateway):
    feed = Feed(client, gateway)
    ada, _ = await acme.person('ada@acme.com')
    [first] = [r for r in await feed.poll() if r['producer']['id'] == ada]
    await client.delete(f'/portal/team/{ada}', headers=acme.admin)
    [gone] = await feed.poll()

    async with shared_plane() as db:
        await add_member(db, acme.org_id, ada)
    [again] = await feed.poll()
    await ProducerFeed().reconcile()

    assert (gone['incarnation'], gone['removed']) == (first['incarnation'], True)
    assert (again['active'], again['removed']) == (True, False)
    assert again['incarnation'] != first['incarnation']
    assert await feed.poll() == []
    stored = [(r.incarnation, r.removed) for r in await ProducerFeedDB().after(0, limit=100) if r.producer_id == ada]
    assert sorted(stored) == sorted([(first['incarnation'], True), (again['incarnation'], False)])


async def test_an_app_is_active_with_the_cloud_suspended_without_and_removed_when_deleted(client, acme, gateway):
    feed = Feed(client, gateway)
    app = await acme.app()
    await feed.poll()

    await client.post(f'/portal/apps/{app.client_id}/vm-credential', headers=acme.admin)
    enabled = await feed.poll()
    [credential] = (await client.get('/portal/vm-credentials', headers=acme.admin)).json()['credentials']
    await client.delete(f'/portal/vm-credentials/{credential["credential_id"]}', headers=acme.admin)
    disabled = await feed.poll()
    await client.delete(f'/portal/apps/{app.client_id}', headers=acme.admin)
    deleted = await feed.poll()

    assert _fate(enabled) == [(app.client_id, True, False)]
    assert _fate(disabled) == [(app.client_id, False, False)]
    assert _fate(deleted) == [(app.client_id, False, True)]
    assert deleted[0]['profile_version'] == enabled[0]['profile_version']


async def test_a_deleted_account_is_removed_and_stays_removed(client, acme, gateway):
    feed = Feed(client, gateway)
    ada, session = await acme.person('ada@acme.com')
    await feed.poll()

    deleted = await client.delete('/portal/me', headers=session)
    told = await feed.poll()
    await ProducerFeed().reconcile()

    assert deleted.status_code == 200, deleted.text
    assert _fate(told) == [(ada, False, True)]
    assert await feed.poll() == []


async def test_moving_to_a_group_with_another_profile_changes_the_version(client, acme, gateway):
    feed = Feed(client, gateway)
    browser = (await client.post(f'/portal/orgs/{acme.org_id}/vm-profiles', json=BROWSER, headers=acme.admin)).json()
    legal = await acme.group('Legal')
    await client.put(
        f'/portal/orgs/{acme.org_id}/groups/{legal}/vm-profile', json={'profile_id': browser['id']}, headers=acme.admin
    )
    ada, _ = await acme.person('ada@acme.com')
    [before] = [r for r in await feed.poll() if r['producer']['id'] == ada]

    moved = await client.post(f'/portal/groups/{legal}/members', json={'user_id': ada}, headers=acme.admin)
    [after] = await feed.poll()

    assert moved.status_code == 200, moved.text
    served = await WorkedOut(client, acme).of('person', ada)
    assert after['producer'] == {'kind': 'person', 'id': ada}
    assert after['profile_version'] == served['profile_version']
    assert after['packages_version'] == served['packages_version']
    assert after['profile'] == served['profile']
    assert after['profile']['packages']['apt'] == ['chromium']
    assert after['profile_version'] != before['profile_version']
    assert after['packages_version'] != before['packages_version']


async def test_editing_a_profile_lists_everybody_who_has_it(client, acme, gateway):
    feed = Feed(client, gateway)
    ada, _ = await acme.person('ada@acme.com')
    app = await acme.app()
    before = {r['producer']['id']: r for r in await feed.poll()}
    default_id = (await client.get(f'/portal/orgs/{acme.org_id}/vm-profiles', headers=acme.admin)).json()[
        'default_profile_id'
    ]

    await client.put(
        f'/portal/orgs/{acme.org_id}/vm-profiles/{default_id}',
        json={**BROWSER, 'name': 'Standard', 'packages': {'apt': [], 'pip': [], 'npm': []}, 'memory_mib': 2048},
        headers=acme.admin,
    )
    after = {r['producer']['id']: r for r in await feed.poll()}

    assert set(after) == {acme.admin_id, ada, app.client_id}
    assert all(after[p]['profile_version'] != before[p]['profile_version'] for p in after)
    assert {after[p]['profile']['memory_mib'] for p in after} == {2048}
    # The ceiling changed, not the packages: no layer to rebuild.
    assert all(after[p]['packages_version'] == before[p]['packages_version'] for p in after)


async def test_lowering_a_cap_lists_the_producers_it_clamps(client, acme, gateway):
    feed = Feed(client, gateway)
    big = {**BROWSER, 'memory_mib': 8192}
    browser = (await client.post(f'/portal/orgs/{acme.org_id}/vm-profiles', json=big, headers=acme.admin)).json()
    app = await acme.app()
    await client.put(
        f'/portal/orgs/{acme.org_id}/apps/{app.client_id}/vm-profile',
        json={'profile_id': browser['id']},
        headers=acme.admin,
    )
    await feed.poll()
    await make_super_admin(acme.admin_id)

    # the Standard profile's 4096 MiB fits; the app's 8192 does not
    await client.put(
        '/portal/vm-caps',
        json={'max_memory_mib': 4096, 'max_vcpus': 4, 'max_idle_before_pause_s': 3600},
        headers=acme.admin,
    )

    [clamped] = await feed.poll()
    assert _who([clamped]) == [('app', app.client_id, False)]
    assert (clamped['profile']['memory_mib'], clamped['profile']['vcpus']) == (4096, 2)


async def test_pages_follow_the_cursor_and_miss_nothing(client, acme, gateway):
    feed = Feed(client, gateway)
    for n in range(4):
        await acme.person(f'p{n}@acme.com')

    pages = [await feed.poll(limit=2), await feed.poll(limit=2), await feed.poll(limit=2)]

    assert [len(page) for page in pages] == [2, 2, 1]
    assert len({row['producer']['id'] for page in pages for row in page}) == 5


async def test_a_cursor_it_did_not_give_is_refused(client, gateway):
    response = await client.get('/platform/producers', params={'since': 'yesterday'}, headers=gateway)

    assert response.status_code == 400


async def test_organisations_are_listed_apart(client, acme, gateway):
    globex = await CloudOrg(client, slug='globex').create()
    feed = Feed(client, gateway)

    rows = await feed.poll()

    assert {(r['org_id'], r['producer']['id']) for r in rows} == {
        (acme.org_id, acme.admin_id),
        (globex.org_id, globex.admin_id),
    }


async def test_a_number_is_never_committed_after_a_larger_one(client, acme, gateway, monkeypatch):
    """Two gateways polling at once: the second waits for the first to commit before numbering.

    The first numbers inside a transaction holding the feed's lock; a
    change made meanwhile is marked, and the second, once it has the lock,
    numbers it after everything the first committed. Had the second
    drawn numbers too and committed first, a cursor past them would skip
    the first's when they landed.
    """
    feed = Feed(client, gateway)
    await feed.poll()
    ada, _ = await acme.person('ada@acme.com')
    computing = asyncio.Event()
    finish = asyncio.Event()
    real = ProducerStates.of

    async def slow(self, kind: str, ids: list[str]) -> list:
        computing.set()
        await finish.wait()
        return await real(self, kind, ids)

    monkeypatch.setattr(ProducerStates, 'of', slow)
    first = asyncio.create_task(ProducerFeed().drain())
    await computing.wait()
    monkeypatch.setattr(ProducerStates, 'of', real)
    max_id, _ = await acme.person('max@acme.com')
    second = asyncio.create_task(ProducerFeed().drain())
    await asyncio.sleep(0.2)
    waited = not second.done()
    finish.set()

    assert await first == 1
    assert await second == 1
    assert waited
    rows = await feed.poll()
    assert _who(rows) == [('person', ada, True), ('person', max_id, True)]


async def test_a_deleted_organisations_people_are_reported_gone(client, acme, gateway):
    feed = Feed(client, gateway)
    await feed.poll()

    deleted = await client.delete(f'/portal/organizations/{acme.org_id}', headers=acme.admin)

    assert deleted.status_code == 200, deleted.text
    assert _fate(await feed.poll()) == [(acme.admin_id, False, True)]


def _later(monkeypatch, days: int) -> None:
    """The clock the feed's store reads, moved ahead."""
    monkeypatch.setattr(
        'app.db.sql.producer_feed.datetime',
        type('Later', (), {'now': staticmethod(lambda tz=None: datetime.now(timezone.utc) + timedelta(days=days))}),
    )


async def _rows() -> list[tuple[str, bool, bool]]:
    return [(row.producer_id, row.active, row.removed) for row in await ProducerFeedDB().after(0, limit=1000)]


class TestRetention:
    async def test_removed_and_handed_over_it_is_deleted_thirty_days_later(self, client, acme, gateway, monkeypatch):
        feed = Feed(client, gateway)
        app = await acme.app()
        await feed.poll()
        await client.delete(f'/portal/apps/{app.client_id}', headers=acme.admin)
        await feed.poll()

        _later(monkeypatch, 29)
        await ProducerFeed().close_out()
        kept = await _rows()
        _later(monkeypatch, 31)
        removed = await retention.run_once()

        assert (app.client_id, False, True) in kept
        assert removed.producer_feed == 1
        assert app.client_id not in [pid for pid, _, _ in await _rows()]

    async def test_removed_but_not_yet_handed_over_it_is_kept(self, client, acme, gateway, monkeypatch):
        feed = Feed(client, gateway)
        app = await acme.app()
        await feed.poll()
        await client.delete(f'/portal/apps/{app.client_id}', headers=acme.admin)
        await ProducerFeed().drain()

        _later(monkeypatch, 60)
        await retention.run_once()

        assert (app.client_id, False, True) in await _rows()

    async def test_inactive_but_not_removed_it_is_never_purged(self, client, acme, gateway, monkeypatch):
        """Its VM is still there, suspended; the row is how the gateway knows it."""
        feed = Feed(client, gateway)
        ada, _ = await acme.person('ada@acme.com')
        await feed.poll()
        await client.patch(f'/portal/team/{ada}/status', json={'status': 'deactivated'}, headers=acme.admin)
        await feed.poll()

        _later(monkeypatch, 400)
        await retention.run_once()

        assert (ada, False, False) in await _rows()

    async def test_an_erased_person_is_removed_and_deleted_once_handed_over(self, client, acme, gateway):
        feed = Feed(client, gateway)
        ada, _ = await acme.person('ada@acme.com')
        await feed.poll()

        erased = await client.post('/portal/erasure', json={'email': 'ada@acme.com'}, headers=acme.admin)
        before = await _rows()
        told = await feed.poll()
        await retention.run_once()

        assert erased.status_code == 200, erased.text
        assert (ada, False, True) in before
        assert _fate(told) == [(ada, False, True)]
        assert ada not in [pid for pid, _, _ in await _rows()]

    async def test_an_erasure_request_overrides_the_retention(self, client, acme, gateway):
        feed = Feed(client, gateway)
        ada, _ = await acme.person('ada@acme.com')
        await feed.poll()
        await client.delete(f'/portal/team/{ada}', headers=acme.admin)
        [scheduled] = await feed.poll()

        await client.post('/portal/erasure', json={'email': 'ada@acme.com'}, headers=acme.admin)
        [now] = await feed.poll()
        await retention.run_once()

        assert datetime.fromisoformat(scheduled['erase_at']) > datetime.now(timezone.utc) + timedelta(days=29)
        assert datetime.fromisoformat(now['erase_at']) <= datetime.now(timezone.utc)
        assert ada not in [pid for pid, _, _ in await _rows()]
