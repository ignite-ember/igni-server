"""Starting a group from another one.

A group is the whole list of what its people get, so the only way to say
"the same as that team, plus this" is to copy it. Any group can be the
template, including the Default one that holds what igni ships — which
is what makes "the standard set, minus the coding agents" two ordinary
steps instead of a merge rule.

A copy, not a link: the new group must be free to diverge, and the one
it came from must not change under it afterwards.
"""

import pytest

from app.db.sql.group_extensions import GroupExtensionsDB
from app.services.portal.default_group import DEFAULT_GROUP_NAME, DefaultGroup, seed_entries
from tests.conftest import add_member, auth_header, create_org, create_user, make_token


@pytest.fixture
async def admin(db):
    await create_user(db, user_id='admin-1', email='admin@test.com')
    org = await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')
    await add_member(db, org_id=str(org.id), user_id='admin-1', role='admin')
    return auth_header(make_token('admin-1', org_id=str(org.id))), str(org.id)


async def _group_with(client, headers, name, entries):
    group = (await client.post('/portal/groups', json={'name': name}, headers=headers)).json()
    for entry_name in entries:
        await client.post(
            f'/portal/groups/{group["id"]}/entries',
            json={
                'kind': 'agents',
                'entry_name': entry_name,
                'content': f'---\nname: {entry_name}\n---\nBody.',
                'content_type': 'markdown',
            },
            headers=headers,
        )
    return group


async def _entry_names(client, headers, group_id):
    resp = await client.get(f'/portal/groups/{group_id}/entries', headers=headers)
    return {o['entry_name'] for o in resp.json()['entries']}


async def _another_org(client, db):
    """A second org with one group, for the cross-tenant checks."""
    await create_user(db, user_id='other-1', email='other@test.com')
    org = await create_org(db, owner_user_id='other-1', slug='other', display_name='Other')
    await add_member(db, org_id=str(org.id), user_id='other-1', role='admin')
    headers = auth_header(make_token('other-1', org_id=str(org.id)))
    return headers, await _group_with(client, headers, 'Theirs', ['secret-agent'])


class TestStartingEmpty:
    async def test_a_group_with_no_template_holds_nothing(self, client, admin):
        headers, _ = admin
        group = (await client.post('/portal/groups', json={'name': 'Legal'}, headers=headers)).json()

        assert await _entry_names(client, headers, group['id']) == set()


class TestCopyingAnother:
    async def test_the_entries_come_across(self, client, admin):
        headers, _ = admin
        source = await _group_with(client, headers, 'Engineering', ['reviewer', 'debugger'])

        copy = (
            await client.post(
                '/portal/groups',
                json={'name': 'Platform', 'template_group_id': source['id']},
                headers=headers,
            )
        ).json()

        assert await _entry_names(client, headers, copy['id']) == {'reviewer', 'debugger'}

    async def test_what_each_entry_says_comes_across_too(self, client, admin):
        """A name-only copy would look right and behave like an empty
        group."""
        headers, _ = admin
        source = await _group_with(client, headers, 'Engineering', ['reviewer'])

        copy = (
            await client.post(
                '/portal/groups',
                json={'name': 'Platform', 'template_group_id': source['id']},
                headers=headers,
            )
        ).json()

        entries = (await client.get(f'/portal/groups/{copy["id"]}/entries', headers=headers)).json()
        assert 'Body.' in entries['entries'][0]['content']

    async def test_editing_the_copy_leaves_the_original(self, client, admin):
        """A copy, not a link."""
        headers, _ = admin
        source = await _group_with(client, headers, 'Engineering', ['reviewer'])
        copy = (
            await client.post(
                '/portal/groups',
                json={'name': 'Platform', 'template_group_id': source['id']},
                headers=headers,
            )
        ).json()

        copied = (await client.get(f'/portal/groups/{copy["id"]}/entries', headers=headers)).json()
        await client.delete(
            f'/portal/groups/{copy["id"]}/entries/{copied["entries"][0]["id"]}',
            headers=headers,
        )

        assert await _entry_names(client, headers, source['id']) == {'reviewer'}

    async def test_editing_the_original_leaves_the_copy(self, client, admin):
        headers, _ = admin
        source = await _group_with(client, headers, 'Engineering', ['reviewer'])
        copy = (
            await client.post(
                '/portal/groups',
                json={'name': 'Platform', 'template_group_id': source['id']},
                headers=headers,
            )
        ).json()

        originals = (await client.get(f'/portal/groups/{source["id"]}/entries', headers=headers)).json()
        await client.delete(
            f'/portal/groups/{source["id"]}/entries/{originals["entries"][0]["id"]}',
            headers=headers,
        )

        assert await _entry_names(client, headers, copy['id']) == {'reviewer'}

    async def test_a_template_from_another_org_is_refused(self, client, admin, db):
        headers, _ = admin
        _, theirs = await _another_org(client, db)

        resp = await client.post(
            '/portal/groups',
            json={'name': 'Mine', 'template_group_id': theirs['id']},
            headers=headers,
        )

        assert resp.status_code == 404

    async def test_a_template_that_does_not_exist_is_refused(self, client, admin):
        headers, _ = admin

        resp = await client.post(
            '/portal/groups',
            json={'name': 'Legal', 'template_group_id': '00000000-0000-0000-0000-000000000000'},
            headers=headers,
        )

        assert resp.status_code == 404


class TestTheStandardSet:
    async def test_the_default_group_is_a_template_like_any_other(self, client, admin):
        """Which is what makes "the standard set, minus the coding
        agents" two steps rather than a merge rule."""
        headers, _ = admin
        await DefaultGroup().ensure_for_user('admin-1')
        groups = (await client.get('/portal/groups', headers=headers)).json()['groups']
        default = next(g for g in groups if g['name'] == DEFAULT_GROUP_NAME)

        legal = (
            await client.post(
                '/portal/groups',
                json={'name': 'Legal', 'template_group_id': default['id']},
                headers=headers,
            )
        ).json()

        assert await _entry_names(client, headers, legal['id']) == {name for _kind, name, _content in seed_entries()}

    async def test_and_then_it_can_be_cut_down(self, client, admin):
        headers, _ = admin
        await DefaultGroup().ensure_for_user('admin-1')
        groups = (await client.get('/portal/groups', headers=headers)).json()['groups']
        default = next(g for g in groups if g['name'] == DEFAULT_GROUP_NAME)
        legal = (
            await client.post(
                '/portal/groups',
                json={'name': 'Legal', 'template_group_id': default['id']},
                headers=headers,
            )
        ).json()

        entries = (await client.get(f'/portal/groups/{legal["id"]}/entries', headers=headers)).json()
        reviewer = next(o for o in entries['entries'] if o['entry_name'] == 'reviewer')
        await client.delete(f'/portal/groups/{legal["id"]}/entries/{reviewer["id"]}', headers=headers)

        assert 'reviewer' not in await _entry_names(client, headers, legal['id'])
        # And the org's standard set is untouched.
        assert 'reviewer' in await _entry_names(client, headers, default['id'])


class TestEntriesStayInTheirOrg:
    """The group endpoints were org-scoped and the entry endpoints under
    them were not, so an admin of one org could read or edit another
    org's entries by knowing the group id. Closed by routing every entry
    path through a group lookup that takes the org.
    """

    async def test_another_org_cannot_read_them(self, client, admin, db):
        headers, _ = admin
        _, theirs = await _another_org(client, db)

        resp = await client.get(f'/portal/groups/{theirs["id"]}/entries', headers=headers)

        assert resp.status_code == 404

    async def test_another_org_cannot_add_one(self, client, admin, db):
        headers, _ = admin
        _, theirs = await _another_org(client, db)

        resp = await client.post(
            f'/portal/groups/{theirs["id"]}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'mine',
                'content': '---\nname: mine\n---\nBody.',
                'content_type': 'markdown',
            },
            headers=headers,
        )

        assert resp.status_code == 404

    async def test_another_org_cannot_delete_one(self, client, admin, db):
        headers, _ = admin
        their_headers, theirs = await _another_org(client, db)
        entries = (await client.get(f'/portal/groups/{theirs["id"]}/entries', headers=their_headers)).json()

        resp = await client.delete(
            f'/portal/groups/{theirs["id"]}/entries/{entries["entries"][0]["id"]}', headers=headers
        )

        assert resp.status_code == 404
        # And it is still there.
        after = (await client.get(f'/portal/groups/{theirs["id"]}/entries', headers=their_headers)).json()
        assert after['total'] == 1


class TestTheCopyAction:
    """`POST /portal/groups/{id}/copy`: everything a group gives its people, under a new name, with nobody in it."""

    async def test_entries_extensions_and_settings_come_across(self, client, admin, db):
        headers, org_id = admin
        source = await _group_with(client, headers, 'Engineering', ['reviewer'])
        await client.patch(
            f'/portal/groups/{source["id"]}',
            json={'description': 'The builders', 'theme': {'accent': '#112233'}},
            headers=headers,
        )
        await GroupExtensionsDB().enable(org_id, source['id'], 'igni.codeindex', by_user_id=None)

        resp = await client.post(f'/portal/groups/{source["id"]}/copy', json={'name': 'Platform'}, headers=headers)

        assert resp.status_code == 201, resp.text
        copy = resp.json()
        assert copy['name'] == 'Platform'
        assert copy['description'] == 'The builders'
        assert copy['theme']['accent'] == '#112233'
        assert copy['is_default'] is False
        assert await _entry_names(client, headers, copy['id']) == {'reviewer'}
        assert await GroupExtensionsDB().enabled_in(org_id, copy['id']) == {'igni.codeindex'}

    async def test_nobody_comes_across(self, client, admin, db):
        headers, org_id = admin
        source = await _group_with(client, headers, 'Engineering', [])
        await client.post(f'/portal/groups/{source["id"]}/members', json={'user_id': 'admin-1'}, headers=headers)

        copy = (
            await client.post(f'/portal/groups/{source["id"]}/copy', json={'name': 'Platform'}, headers=headers)
        ).json()

        members = await client.get(f'/portal/groups/{copy["id"]}/members', headers=headers)
        assert members.json()['members'] == []
        stayed = await client.get('/portal/users/admin-1/group', headers=headers)
        assert stayed.json()['id'] == source['id']

    async def test_the_default_group_copies_into_an_ordinary_one(self, client, admin):
        headers, _ = admin
        groups = (await client.get('/portal/groups', headers=headers)).json()['groups']
        default_id = next(g['id'] for g in groups if g['is_default'])

        copy = (await client.post(f'/portal/groups/{default_id}/copy', json={'name': 'Mine'}, headers=headers)).json()

        assert copy['is_default'] is False
        copied = (await client.get(f'/portal/groups/{copy["id"]}/entries', headers=headers)).json()['entries']
        assert sorted((e['kind'], e['entry_name']) for e in copied) == sorted(
            (kind, name) for kind, name, _ in seed_entries()
        )

    async def test_a_taken_name_is_refused(self, client, admin):
        headers, _ = admin
        source = await _group_with(client, headers, 'Engineering', [])

        resp = await client.post(f'/portal/groups/{source["id"]}/copy', json={'name': 'Engineering'}, headers=headers)

        assert resp.status_code == 409

    async def test_only_an_admin_copies(self, client, admin, db):
        headers, org_id = admin
        source = await _group_with(client, headers, 'Engineering', [])
        await create_user(db, user_id='member-1', email='member@test.com')
        await add_member(db, org_id=org_id, user_id='member-1', role='member')

        resp = await client.post(
            f'/portal/groups/{source["id"]}/copy',
            json={'name': 'Mine'},
            headers=auth_header(make_token('member-1', org_id=org_id)),
        )

        assert resp.status_code == 403
