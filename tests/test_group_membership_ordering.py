"""A new hire who starts igni early still gets their company's policy.

A person belongs to exactly one group in their organisation, enforced by
a unique constraint. The order things happen in used to matter:

1. a new hire is added to the company org as a member;
2. they start igni before an admin puts them in the company's group;
3. the pack endpoint puts them in the ``Default`` group;
4. the admin's "add to group" then failed with 409, permanently, and
   the new hire silently kept the default policy.

Now everybody is always in exactly one group — Default until placed —
so putting somebody in a group *is* moving them, and step four just
works. Taking them out of a group puts them back in Default.
"""

from __future__ import annotations

import pytest

from tests.conftest import add_member, auth_header, create_org, create_user, make_token


@pytest.fixture
async def acme(client, db):
    """A company with a policy group, and a new hire in the org but not
    yet in the group."""
    await create_user(db, user_id='newhire', email='n@acme.com')
    await create_user(db, user_id='boss', email='boss@acme.com')
    company = await create_org(db, owner_user_id='boss', slug='acme', display_name='Acme')
    await add_member(db, org_id=str(company.id), user_id='boss', role='admin')
    await add_member(db, org_id=str(company.id), user_id='newhire', role='member')

    boss = auth_header(make_token('boss', org_id=str(company.id)))
    created = await client.post('/portal/groups', json={'name': 'acme-policy'}, headers=boss)
    group_id = created.json()['id']
    await client.post(
        f'/portal/groups/{group_id}/entries',
        json={
            'kind': 'agents',
            'entry_name': 'acme-agent',
            'content': '---\nname: acme-agent\ndescription: d\n---\nbody',
            'content_type': 'markdown',
        },
        headers=boss,
    )
    return {
        'group': group_id,
        'boss': boss,
        'newhire': auth_header(make_token('newhire', org_id=str(company.id))),
    }


async def _pack(client, headers):
    response = await client.get('/portal/me/group/pack', headers=headers)
    if response.status_code != 200 or response.text in ('', 'null'):
        return None
    return response.json()


class TestTheOrderNoLongerMatters:
    async def test_starting_early_puts_them_in_the_default_group(self, client, acme):
        pack = await _pack(client, acme['newhire'])

        assert pack is not None
        assert pack['group_name'] == 'Default'

    async def test_adding_them_afterwards_moves_them(self, client, acme):
        await _pack(client, acme['newhire'])

        response = await client.post(
            f'/portal/groups/{acme["group"]}/members',
            json={'user_id': 'newhire'},
            headers=acme['boss'],
        )
        assert response.status_code == 200, response.text
        assert [member['user_id'] for member in response.json()['members']] == ['newhire']

        pack = await _pack(client, acme['newhire'])
        assert pack['group_name'] == 'acme-policy'
        assert [entry['entry_name'] for entry in pack['entries']] == ['acme-agent']

    async def test_adding_them_twice_changes_nothing(self, client, acme):
        for _ in range(2):
            response = await client.post(
                f'/portal/groups/{acme["group"]}/members',
                json={'user_id': 'newhire'},
                headers=acme['boss'],
            )
            assert response.status_code == 200, response.text

        assert response.json()['total'] == 1

    async def test_taking_them_out_puts_them_back_in_default(self, client, acme):
        await client.post(
            f'/portal/groups/{acme["group"]}/members',
            json={'user_id': 'newhire'},
            headers=acme['boss'],
        )

        removed = await client.delete(f'/portal/groups/{acme["group"]}/members/newhire', headers=acme['boss'])
        assert removed.status_code == 204, removed.text

        pack = await _pack(client, acme['newhire'])
        assert pack['group_name'] == 'Default'


class TestTheHappyOrder:
    async def test_added_before_they_start_is_fine(self, client, acme):
        """The order that works, so the test above is about ordering
        rather than about groups being broken."""
        added = await client.post(
            f'/portal/groups/{acme["group"]}/members',
            json={'user_id': 'newhire'},
            headers=acme['boss'],
        )
        assert added.status_code == 200, added.text

        pack = await _pack(client, acme['newhire'])

        assert pack['group_name'] == 'acme-policy'
        assert [entry['entry_name'] for entry in pack['entries']] == ['acme-agent']


class TestThePackItself:
    async def test_it_is_not_stored_by_any_cache(self, client, acme):
        """It carries a group's agents, hooks and scripts, so a shared
        cache holding one org's pack and serving it to another would be
        the tenant boundary gone.

        ``no-store`` rather than the ``private, max-age=60`` the endpoint
        used to ask for: ``DisableCacheMiddleware`` overwrites
        ``Cache-Control`` on every response, so that header was never
        delivered. The stricter answer is the safe one, and it is now what
        the endpoint says too.
        """
        await client.post(
            f'/portal/groups/{acme["group"]}/members',
            json={'user_id': 'newhire'},
            headers=acme['boss'],
        )

        response = await client.get('/portal/me/group/pack', headers=acme['newhire'])

        assert 'no-store' in response.headers.get('cache-control', '')

    async def test_an_unchanged_pack_revalidates_to_304(self, client, acme):
        """igni polls this from every running session, so the ETag is not
        a nicety — and it has to be stable across requests, which it was
        not until ``fetched_at`` was excluded from the hash."""
        await client.post(
            f'/portal/groups/{acme["group"]}/members',
            json={'user_id': 'newhire'},
            headers=acme['boss'],
        )
        first = await client.get('/portal/me/group/pack', headers=acme['newhire'])
        etag = first.headers['etag']

        second = await client.get(
            '/portal/me/group/pack',
            headers={**acme['newhire'], 'If-None-Match': etag},
        )

        assert second.status_code == 304
        assert second.content == b''

    async def test_a_changed_pack_gets_a_new_etag(self, client, acme):
        """The other half: an ETag that never changes is a pack that never
        updates, which is worse than no caching at all."""
        await client.post(
            f'/portal/groups/{acme["group"]}/members',
            json={'user_id': 'newhire'},
            headers=acme['boss'],
        )
        first = await client.get('/portal/me/group/pack', headers=acme['newhire'])

        await client.post(
            f'/portal/groups/{acme["group"]}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'second-agent',
                'content': '---\nname: second-agent\ndescription: d\n---\nbody',
                'content_type': 'markdown',
            },
            headers=acme['boss'],
        )

        second = await client.get('/portal/me/group/pack', headers=acme['newhire'])

        assert second.headers['etag'] != first.headers['etag']
