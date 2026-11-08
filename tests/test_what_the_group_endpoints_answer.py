"""What the portal's group endpoints answer for the cases `GroupsService` decides.

Creating, copying and the Default group's refusals are pinned in
`test_portal_groups.py`, `test_group_templates.py` and
`test_default_group.py`. These are the rest: a rename onto a name that
is taken, a group the request cannot reach, a missing template's words,
and deleting a group in an organisation whose Default group nobody has
asked for yet.
"""

from __future__ import annotations

import pytest

from app.db.sql.groups import GroupsDB
from tests.conftest import add_member, auth_header, create_org, create_user, make_token


@pytest.fixture
async def admin(db):
    await create_user(db, user_id='admin-1', email='admin@test.com')
    org = await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')
    await add_member(db, org_id=str(org.id), user_id='admin-1', role='admin')
    return auth_header(make_token('admin-1', org_id=str(org.id))), str(org.id)


async def _group(client, headers, name):
    resp = await client.post('/portal/groups', json={'name': name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()['id']


class TestRenaming:
    async def test_onto_another_groups_name_is_refused(self, client, admin):
        headers, _ = admin
        await _group(client, headers, 'Legal')
        eng = await _group(client, headers, 'Eng')

        resp = await client.patch(f'/portal/groups/{eng}', json={'name': 'Legal'}, headers=headers)

        assert resp.status_code == 409
        assert resp.json()['detail'] == "A group named 'Legal' already exists in this organization."

    async def test_a_group_may_keep_its_own_name(self, client, admin):
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')

        resp = await client.patch(
            f'/portal/groups/{legal}', json={'name': 'Legal', 'description': 'Contracts'}, headers=headers
        )

        assert resp.status_code == 200, resp.text
        assert (resp.json()['name'], resp.json()['description']) == ('Legal', 'Contracts')


class TestAGroupOutOfReach:
    @pytest.fixture
    async def theirs(self, client, db):
        await create_user(db, user_id='other-1', email='other@test.com')
        org = await create_org(db, owner_user_id='other-1', slug='other', display_name='Other')
        await add_member(db, org_id=str(org.id), user_id='other-1', role='admin')
        return await _group(client, auth_header(make_token('other-1', org_id=str(org.id))), 'Theirs')

    async def test_another_organisations_group_is_not_found(self, client, admin, theirs):
        headers, _ = admin

        answers = [
            await client.get(f'/portal/groups/{theirs}', headers=headers),
            await client.patch(f'/portal/groups/{theirs}', json={'name': 'Mine'}, headers=headers),
            await client.delete(f'/portal/groups/{theirs}', headers=headers),
            await client.post(f'/portal/groups/{theirs}/copy', json={'name': 'Mine'}, headers=headers),
        ]

        assert [(resp.status_code, resp.json()['detail']) for resp in answers] == [(404, 'Group not found')] * 4

    async def test_a_group_that_does_not_exist_is_not_found(self, client, admin):
        headers, _ = admin

        resp = await client.get('/portal/groups/00000000-0000-0000-0000-000000000000', headers=headers)

        assert (resp.status_code, resp.json()['detail']) == (404, 'Group not found')

    async def test_a_missing_template_says_so(self, client, admin):
        headers, _ = admin

        resp = await client.post(
            '/portal/groups',
            json={'name': 'Legal', 'template_group_id': '00000000-0000-0000-0000-000000000000'},
            headers=headers,
        )

        assert resp.status_code == 404
        assert resp.json()['detail'] == 'The group to copy from does not exist in this organization.'


class TestReadingOne:
    async def test_the_default_groups_people_are_placed_before_they_are_counted(self, client, admin, db):
        """Reading the Default group is one of the views that puts everybody in a group."""
        headers, org_id = admin
        default_id = next(
            g['id'] for g in (await client.get('/portal/groups', headers=headers)).json()['groups'] if g['is_default']
        )
        await create_user(db, user_id='new-1', email='new@test.com')
        await add_member(db, org_id=org_id, user_id='new-1', role='member')

        resp = await client.get(f'/portal/groups/{default_id}', headers=headers)

        assert resp.status_code == 200
        assert resp.json()['member_count'] == 2

    async def test_reading_another_group_places_nobody(self, client, admin, db):
        headers, org_id = admin
        legal = await _group(client, headers, 'Legal')
        await create_user(db, user_id='new-1', email='new@test.com')
        await add_member(db, org_id=org_id, user_id='new-1', role='member')

        resp = await client.get(f'/portal/groups/{legal}', headers=headers)

        assert resp.status_code == 200
        assert resp.json()['member_count'] == 0
        assert await GroupsDB().get_group_by_name(org_id, name='Default') is None


class TestDeleting:
    async def test_the_default_group_is_made_first_when_nobody_has_asked_for_it(self, client, admin):
        """Its people have to go somewhere; deleting is not refused for want of a Default group."""
        headers, org_id = admin
        legal = await _group(client, headers, 'Legal')
        assert await GroupsDB().get_group_by_name(org_id, name='Default') is None

        resp = await client.delete(f'/portal/groups/{legal}', headers=headers)

        assert resp.status_code == 204
        assert await GroupsDB().get_group_by_name(org_id, name='Default') is not None
        assert await GroupsDB().get_group(org_id, group_id=legal) is None
