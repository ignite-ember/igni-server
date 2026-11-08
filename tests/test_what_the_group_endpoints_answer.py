"""What the portal's group endpoints answer for the cases `GroupsService` decides.

Creating, copying and the Default group's refusals are pinned in
`test_portal_groups.py`, `test_group_templates.py` and
`test_default_group.py`. These are the rest: a rename onto a name that
is taken, a group the request cannot reach, a missing template's words,
deleting a group in an organisation whose Default group nobody has
asked for yet, an entry for a group deleted meanwhile, the words for
each entry rule, what an admin is warned about, and who may be put in
a group.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
import pytest

from app.db.sql.groups import GroupsDB
from app.services.portal.default_group import DefaultGroup
from app.services.portal.group_access import GroupAccess
from app.services.portal.group_members_service import GroupMembersService
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

    async def test_a_template_in_another_organisation_is_missing_too(self, client, admin, theirs):
        headers, _ = admin

        resp = await client.post('/portal/groups', json={'name': 'Mine', 'template_group_id': theirs}, headers=headers)

        assert (resp.status_code, resp.json()['detail']) == (
            404,
            'The group to copy from does not exist in this organization.',
        )


class TestWhatIsNotInTheGroup:
    async def test_an_entry_it_does_not_hold_is_not_found(self, client, admin):
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')
        missing = f'/portal/groups/{legal}/entries/00000000-0000-0000-0000-000000000000'

        answers = [
            await client.patch(missing, json={'enabled': False}, headers=headers),
            await client.delete(missing, headers=headers),
        ]

        assert [(resp.status_code, resp.json()['detail']) for resp in answers] == [(404, 'Entry not found')] * 2

    async def test_somebody_it_does_not_hold_is_not_found(self, client, admin):
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')

        resp = await client.delete(f'/portal/groups/{legal}/members/admin-1', headers=headers)

        assert (resp.status_code, resp.json()['detail']) == (404, 'Member not found')

    async def test_nobody_is_taken_out_of_default_and_they_are_told_how_to_move_them(self, client, admin):
        headers, _ = admin
        groups = (await client.get('/portal/groups', headers=headers)).json()['groups']
        default = next(g['id'] for g in groups if g['is_default'])

        resp = await client.delete(f'/portal/groups/{default}/members/admin-1', headers=headers)

        assert (resp.status_code, resp.json()['detail']) == (
            409,
            'Everybody is in exactly one group, and the Default group is where '
            'people go when they leave any other — so nobody leaves it except by '
            'being moved into another group: POST /portal/groups/{group_id}/members.',
        )


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


async def _post_entry(client, headers, group_id, **fields):
    body = {'kind': 'agents', 'entry_name': 'reviewer', 'content': '# reviewer', 'content_type': 'markdown', **fields}
    return await client.post(f'/portal/groups/{group_id}/entries', json=body, headers=headers)


class TestAnEntryForAGroupDeletedMeanwhile:
    async def test_is_a_server_error_not_already_there(self, app, admin, monkeypatch):
        """The group passes the writable check, then goes before the insert: the foreign key fails, not the name."""
        headers, org_id = admin
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test/v1') as client:
            legal = await _group(client, headers, 'Legal')
        checked = GroupAccess.writable

        async def writable_then_deleted(self, *, org_id: str, group_id: str):
            group = await checked(self, org_id=org_id, group_id=group_id)
            await DefaultGroup().ensure_for_org(org_id, created_by_user_id='admin-1')
            await GroupsDB().delete_group(org_id, group_id=group_id)
            return group

        monkeypatch.setattr(GroupAccess, 'writable', writable_then_deleted)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url='http://test/v1') as client:
            resp = await _post_entry(client, headers, legal)

        assert await GroupsDB().get_group(org_id, group_id=legal) is None
        assert resp.status_code == 500
        assert 'already exists' not in resp.text


class TestAnEntryThatBreaksARule:
    """The words the portal shows for each entry rule, over HTTP."""

    async def test_an_unknown_kind_lists_the_kinds_there_are(self, client, admin):
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')

        resp = await _post_entry(client, headers, legal, kind='macros')

        assert resp.status_code == 422
        assert resp.json()['detail'] == (
            'Invalid kind. Must be one of: agents, commands, hooks, mcps, output-styles, plugins, '
            'rules, scripts, settings, skills, tools, workflows'
        )

    async def test_a_content_type_igni_never_reads_lists_the_ones_it_does(self, client, admin):
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')

        resp = await _post_entry(client, headers, legal, content_type='toml')

        assert resp.status_code == 422
        assert resp.json()['detail'] == (
            'Invalid content_type. Must be one of: javascript, json, markdown, python, shell, yaml'
        )

    async def test_a_content_type_the_kind_is_not_read_as_says_which_it_needs(self, client, admin):
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')

        resp = await _post_entry(client, headers, legal, kind='hooks', content='{}', content_type='markdown')

        assert (resp.status_code, resp.json()['detail']) == (
            422,
            "Kind 'hooks' requires content_type 'json' (igni's loader only parses that format).",
        )

    async def test_a_model_nothing_serves_is_reported_before_a_bad_content_type(self, client, admin, monkeypatch):
        """The served check sits between the kind rules and the content-type rules."""

        async def _served(self):
            return [('igni-1.0-27b', 'http://gpu-01:8000/v1')]

        monkeypatch.setattr('app.services.ai.backend_pool.InferenceBackendPool.list_available_models', _served)
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')

        resp = await _post_entry(client, headers, legal, model='legal-reveiwer', content_type='toml')

        assert (resp.status_code, resp.json()['detail']) == (
            409,
            "No inference node serves 'legal-reveiwer'. Available: igni-1.0-27b.",
        )

    async def test_a_model_on_an_entry_that_does_not_exist_is_not_found(self, client, admin):
        """Without a row there is no kind to check the model against; the store's 404 answers."""
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')

        resp = await client.patch(
            f'/portal/groups/{legal}/entries/00000000-0000-0000-0000-000000000000',
            json={'model': ''},
            headers=headers,
        )

        assert (resp.status_code, resp.json()['detail']) == (404, 'Entry not found')


HOOK_RUNNING_A_SCRIPT = '{"event": "PreToolUse", "command": "bash {group_scripts}/pre-pr-review.sh"}'


class TestWhatAnAdminIsWarnedAbout:
    async def test_a_hook_naming_a_script_the_group_lacks_is_saved_with_a_warning(self, client, admin):
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')

        resp = await _post_entry(
            client,
            headers,
            legal,
            kind='hooks',
            entry_name='review',
            content=HOOK_RUNNING_A_SCRIPT,
            content_type='json',
        )

        assert resp.status_code == 201, resp.text
        assert [e['entry_name'] for e in resp.json()['entries']] == ['review']
        assert resp.json()['warnings'] == [
            "This hook runs 'pre-pr-review', which the group does not ship. Add a script entry of that "
            'name, or the hook will do nothing on every machine in the group — a missing command '
            'fails silently rather than reporting an error.'
        ]

    async def test_a_hook_whose_script_the_group_ships_says_nothing(self, client, admin):
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')
        await _post_entry(
            client, headers, legal, kind='scripts', entry_name='pre-pr-review', content='exit 0', content_type='shell'
        )

        resp = await _post_entry(
            client,
            headers,
            legal,
            kind='hooks',
            entry_name='review',
            content=HOOK_RUNNING_A_SCRIPT,
            content_type='json',
        )

        assert resp.status_code == 201, resp.text
        assert resp.json()['warnings'] == []

    async def test_an_edited_hook_is_checked_against_the_groups_scripts_too(self, client, admin):
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')
        created = await _post_entry(
            client,
            headers,
            legal,
            kind='hooks',
            entry_name='review',
            content='{"command": "true"}',
            content_type='json',
        )
        entry_id = created.json()['entries'][0]['id']
        assert created.json()['warnings'] == []

        resp = await client.patch(
            f'/portal/groups/{legal}/entries/{entry_id}', json={'content': HOOK_RUNNING_A_SCRIPT}, headers=headers
        )

        assert resp.status_code == 200, resp.text
        assert len(resp.json()['warnings']) == 1
        assert resp.json()['warnings'][0].startswith("This hook runs 'pre-pr-review', which the group does not ship.")

    async def test_an_agent_hears_about_its_name_before_its_tools(self, client, admin):
        """A bad name loses the whole agent, a mistyped tool only one of its tools."""
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')

        resp = await _post_entry(
            client, headers, legal, content='---\nname: <entry-name>\ntools: WebSarch\n---\n\nBody.\n'
        )

        assert resp.status_code == 201, resp.text
        name_warning, tool_warning = resp.json()['warnings']
        assert name_warning.startswith('The `name:` line still reads `<entry-name>` from the template.')
        assert "'WebSarch' (did you mean 'WebSearch'?)" in tool_warning

    async def test_an_edit_that_leaves_the_content_alone_warns_of_nothing(self, client, admin):
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')
        created = await _post_entry(client, headers, legal, content='---\nname: <entry-name>\n---\n\nBody.\n')
        assert created.json()['warnings'] != []

        resp = await client.patch(
            f'/portal/groups/{legal}/entries/{created.json()["entries"][0]["id"]}',
            json={'enabled': False},
            headers=headers,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()['warnings'] == []


class TestPuttingSomebodyInAGroup:
    async def test_somebody_outside_the_organisation_cannot_be_added(self, client, admin, db):
        headers, _ = admin
        legal = await _group(client, headers, 'Legal')
        await create_user(db, user_id='stranger-1', email='stranger@test.com')

        resp = await client.post(f'/portal/groups/{legal}/members', json={'user_id': 'stranger-1'}, headers=headers)

        assert (resp.status_code, resp.json()['detail']) == (404, 'This user is not a member of the organization.')

    async def test_nor_somebody_whose_membership_no_longer_holds(self, client, admin, db):
        headers, org_id = admin
        legal = await _group(client, headers, 'Legal')
        await create_user(db, user_id='leaver-1', email='leaver@test.com')
        await add_member(db, org_id=org_id, user_id='leaver-1', role='member', status='deactivated')

        resp = await client.post(f'/portal/groups/{legal}/members', json={'user_id': 'leaver-1'}, headers=headers)

        assert (resp.status_code, resp.json()['detail']) == (404, 'This user is not a member of the organization.')
        members = (await client.get(f'/portal/groups/{legal}/members', headers=headers)).json()
        assert members['total'] == 0

    async def test_the_answer_lists_who_is_in_the_group_by_name(self, client, admin, db):
        headers, org_id = admin
        legal = await _group(client, headers, 'Legal')
        await create_user(db, user_id='lawyer-1', email='lawyer@test.com', full_name='Lee Lawyer')
        await add_member(db, org_id=org_id, user_id='lawyer-1', role='member')

        resp = await client.post(f'/portal/groups/{legal}/members', json={'user_id': 'lawyer-1'}, headers=headers)

        assert resp.status_code == 200, resp.text
        assert [(m['user_id'], m['email'], m['full_name']) for m in resp.json()['members']] == [
            ('lawyer-1', 'lawyer@test.com', 'Lee Lawyer')
        ]
        assert resp.json()['total'] == 1


class _ForgottenUsers:
    """A user store that no longer has anybody's record."""

    async def get_by_user_id(self, user_id):
        return None


class TestWhoIsWhere:
    async def test_every_active_person_is_listed_with_their_one_group(self, client, admin, db):
        """Asking places everybody not yet in a group in Default first."""
        headers, org_id = admin
        legal = await _group(client, headers, 'Legal')
        for user_id in ('lawyer-1', 'new-1'):
            await create_user(db, user_id=user_id, email=f'{user_id}@test.com')
            await add_member(db, org_id=org_id, user_id=user_id, role='member')
        await client.post(f'/portal/groups/{legal}/members', json={'user_id': 'lawyer-1'}, headers=headers)

        resp = await client.get('/portal/group-memberships', headers=headers)

        assert resp.status_code == 200, resp.text
        assert [(m['user_id'], m['group_name'], m['is_default']) for m in resp.json()['memberships']] == [
            ('admin-1', 'Default', True),
            ('lawyer-1', 'Legal', False),
            ('new-1', 'Default', True),
        ]
        assert resp.json()['memberships'][1]['group_id'] == legal

    async def test_somebody_whose_user_record_is_gone_is_listed_without_a_name(self, client, admin, db):
        headers, org_id = admin
        legal = await _group(client, headers, 'Legal')
        await client.post(f'/portal/groups/{legal}/members', json={'user_id': 'admin-1'}, headers=headers)

        listing = await GroupMembersService(users=_ForgottenUsers()).list_members(org_id=org_id, group_id=legal)  # type: ignore[arg-type]

        assert [(m.user_id, m.email, m.full_name) for m in listing.members] == [('admin-1', None, None)]
