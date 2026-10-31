"""Volumes: a profile names a VM's volume and how far it may grow; an admin grows one producer's.

The platform caps how large a profile may let a volume grow. An org admin,
or the super admin, grows one producer's volume up to its profile's
maximum — never shrinks it — and the feed lists the producer again with
its new `volume_gib` (`docs/service-identity.md` §12, §12.1, §11.3).
"""

from __future__ import annotations

import pytest

from tests.cloud_world import CloudOrg, make_super_admin, platform, shared_plane
from tests.conftest import add_member, audit_rows

pytestmark = pytest.mark.anyio

BIG = {
    'name': 'Big',
    'memory_mib': 1024,
    'vcpus': 1,
    'volume_gib': 20,
    'volume_max_gib': 100,
}


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


def _volume(acme: CloudOrg, producer_id: str, kind: str = 'person') -> str:
    return f'/portal/orgs/{acme.org_id}/producers/{kind}/{producer_id}/volume'


async def _feed(client, gateway, since: str | None = None) -> tuple[list[dict], str]:
    params = {'since': since} if since else {}
    body = (await client.get('/platform/producers', params=params, headers=gateway)).json()
    return list(body['producers']), str(body['cursor'])


async def _served(client, gateway, producer_id: str) -> dict:
    """The person's latest row on the gateway's feed: their volume and the profile their VM gets."""
    rows, _ = await _feed(client, gateway)
    return [row for row in rows if row['producer'] == {'kind': 'person', 'id': producer_id}][-1]


class TestAProfilesVolume:
    async def test_the_default_profile_has_ten_growable_to_fifty(self, client, acme):
        gateway = await platform(client)
        overview = (await client.get(f'/portal/orgs/{acme.org_id}/vm-profiles', headers=acme.admin)).json()

        served = await _served(client, gateway, acme.admin_id)

        [default] = overview['profiles']
        assert (default['volume_gib'], default['volume_max_gib']) == (10, 50)
        assert overview['caps']['max_volume_gib'] == 200
        assert (served['volume_gib'], served['profile']['volume_max_gib']) == (10, 50)

    async def test_a_profile_may_not_start_larger_than_it_grows(self, client, acme):
        response = await client.post(
            f'/portal/orgs/{acme.org_id}/vm-profiles', json={**BIG, 'volume_gib': 200}, headers=acme.admin
        )

        assert response.status_code == 422

    async def test_the_platform_caps_how_far_a_volume_may_grow(self, client, acme):
        over = await client.post(
            f'/portal/orgs/{acme.org_id}/vm-profiles', json={**BIG, 'volume_max_gib': 500}, headers=acme.admin
        )
        await make_super_admin(acme.admin_id)
        raised = await client.put(
            '/portal/vm-caps',
            json={'max_memory_mib': 8192, 'max_vcpus': 4, 'max_idle_before_pause_s': 3600, 'max_volume_gib': 1000},
            headers=acme.admin,
        )
        allowed = await client.post(
            f'/portal/orgs/{acme.org_id}/vm-profiles', json={**BIG, 'volume_max_gib': 500}, headers=acme.admin
        )

        assert over.status_code == 400
        assert 'at most 200 GiB' in over.json()['detail']
        assert raised.json()['max_volume_gib'] == 1000
        assert allowed.status_code == 201, allowed.text

    async def test_the_volume_is_in_the_profile_version(self, client, acme):
        gateway = await platform(client)
        before = await _served(client, gateway, acme.admin_id)
        default_id = before['profile']['profile_id']

        await client.put(
            f'/portal/orgs/{acme.org_id}/vm-profiles/{default_id}',
            json={'name': 'Standard', 'memory_mib': 1024, 'vcpus': 1, 'idle_before_pause_s': 5, 'volume_gib': 15},
            headers=acme.admin,
        )
        after = await _served(client, gateway, acme.admin_id)

        assert after['volume_gib'] == 15
        assert after['profile_version'] != before['profile_version']
        assert after['packages_version'] == before['packages_version']


class TestGrowingOneProducersVolume:
    async def test_an_org_admin_grows_it_and_the_feed_says_so(self, client, acme):
        gateway = await platform(client)
        ada, _ = await acme.person('ada@acme.com')
        _, cursor = await _feed(client, gateway)

        grown = await client.post(_volume(acme, ada), json={'gib': 30}, headers=acme.admin)
        rows, cursor = await _feed(client, gateway, cursor)
        again = await client.post(_volume(acme, ada), json={'gib': 30}, headers=acme.admin)
        unchanged, _ = await _feed(client, gateway, cursor)

        assert grown.status_code == 200, grown.text
        body = grown.json()
        assert (body['volume_gib'], body['volume_max_gib']) == (30, 50)
        assert body['producer'] == {'kind': 'person', 'id': ada}
        assert body['grown_by'] == {'kind': 'person', 'id': acme.admin_id, 'acting_as': 'org_admin'}
        assert [(r['producer']['id'], r['volume_gib']) for r in rows] == [(ada, 30)]
        assert (await _served(client, gateway, ada))['volume_gib'] == 30
        assert again.status_code == 200
        assert unchanged == []
        assert any(row['route'].endswith('/volume') for row in audit_rows())

    async def test_it_is_never_shrunk_nor_grown_past_the_profile(self, client, acme):
        ada, _ = await acme.person('ada@acme.com')
        await client.post(_volume(acme, ada), json={'gib': 30}, headers=acme.admin)

        smaller = await client.post(_volume(acme, ada), json={'gib': 20}, headers=acme.admin)
        larger = await client.post(_volume(acme, ada), json={'gib': 51}, headers=acme.admin)

        assert smaller.status_code == 409
        assert larger.status_code == 400

    async def test_only_admins_of_the_organisation_and_the_super_admin(self, client, acme):
        ada, member = await acme.person('ada@acme.com')
        globex = await CloudOrg(client, slug='globex').create()

        as_member = await client.post(_volume(acme, ada), json={'gib': 20}, headers=member)
        elsewhere = await client.post(_volume(acme, ada), json={'gib': 20}, headers=globex.admin)
        await make_super_admin(globex.admin_id)
        as_super = await client.post(_volume(acme, ada), json={'gib': 20}, headers=globex.admin)

        assert as_member.status_code == 403
        assert elsewhere.status_code == 403
        assert as_super.status_code == 200, as_super.text
        assert as_super.json()['grown_by']['acting_as'] == 'super_admin'

    async def test_a_space_or_an_app_is_grown_the_same_way(self, client, acme):
        app = await acme.app()

        grown = await client.post(_volume(acme, app.client_id, 'app'), json={'gib': 40}, headers=acme.admin)
        nobody = await client.post(_volume(acme, 'nobody', 'app'), json={'gib': 40}, headers=acme.admin)

        assert grown.json()['volume_gib'] == 40
        assert nobody.status_code == 404

    async def test_a_person_added_again_starts_at_the_profiles_size(self, client, acme):
        gateway = await platform(client)
        ada, _ = await acme.person('ada@acme.com')
        await client.post(_volume(acme, ada), json={'gib': 30}, headers=acme.admin)
        await client.delete(f'/portal/team/{ada}', headers=acme.admin)

        async with shared_plane() as db:
            await add_member(db, acme.org_id, ada)
        rows, _ = await _feed(client, gateway)

        live = [r for r in rows if r['producer']['id'] == ada and not r['removed']]
        assert [r['volume_gib'] for r in live] == [10]
        assert (await client.get(_volume(acme, ada), headers=acme.admin)).json()['volume_gib'] == 10
