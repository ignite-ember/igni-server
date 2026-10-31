"""The group every new user lands in.

Agents used to live only in the igni package, so changing what a
team's assistant could do meant shipping a CLI release. The default
group moves them to where the admin already is: each org gets a copy of
the shipped agents, and anyone without a group is put in it.

What matters, in order of how much damage getting it wrong would do:

* It is created once. A second "Default" appearing beside the one an
  admin has been editing would be worse than none at all.
* A user already in a group is left there. Auto-assignment must never
  move somebody the admin placed deliberately.
* Failing to assign costs the user their group, not their session. This
  runs when a CLI starts up.
"""

import pytest

from app.services.portal.default_group import DEFAULT_GROUP_NAME, DefaultGroup, seed_entries
from tests.conftest import add_member, auth_header, create_org, create_user, make_token


@pytest.fixture
async def member(db):
    await create_user(db, user_id='dev-1', email='dev@test.com')
    org = await create_org(db, owner_user_id='dev-1', slug='acme', display_name='Acme')
    await add_member(db, org_id=str(org.id), user_id='dev-1', role='admin')
    return auth_header(make_token('dev-1', org_id=str(org.id))), str(org.id)


class TestTheSeed:
    def test_what_igni_ships_is_vendored(self):
        """Read from the repo, not from an installed igni: the
        server has no reason to depend on the CLI package and a
        deployment may not have it at all."""
        assert len(seed_entries()) > 0

    def test_it_is_not_only_agents(self):
        """A Default group holding agents alone told people that agents
        are all igni comes with. It ships skills, hooks, and the scripts
        those hooks run — igni itself ships nothing, so a hook without
        its script would reference a file nobody sent."""
        kinds = {kind for kind, _name, _content in seed_entries()}
        assert kinds == {'agents', 'skills', 'hooks', 'scripts'}

    def test_the_entry_name_is_the_file_stem(self):
        """igni writes ``<entry_name>.md``, so a group carrying its own
        ``<agent>.<variant>.md`` still reaches the loader as a variant."""
        names = {name for kind, name, _ in seed_entries() if kind == 'agents'}
        assert 'architect' in names

    def test_no_extensions_agents_are_seeded(self):
        """An extension's versions of igni's agents ship with the extension
        and apply while it runs. Seeding them here would hand every
        organisation an extension's prompts whether or not it has it."""
        names = {name for kind, name, _ in seed_entries() if kind == 'agents'}
        assert not [name for name in names if '.' in name], names

    def test_the_skills_behind_the_slash_commands_are_there(self):
        names = {name for kind, name, _ in seed_entries() if kind == 'skills'}
        assert {'commit', 'pr', 'test-plan'} <= names

    def test_agents_and_skills_are_markdown_with_frontmatter(self):
        for kind, name, content in seed_entries():
            if kind in ('agents', 'skills'):
                assert content.startswith('---'), f'{name} has no frontmatter and will not load'

    def test_each_hook_names_the_event_it_fires_on(self):
        """A declaration without one is dropped by the client's cache."""
        import json

        for kind, name, content in seed_entries():
            if kind == 'hooks':
                assert json.loads(content)['event'], name


class TestJoiningIt:
    async def test_a_user_with_no_group_gets_one(self, client, member):
        summary = await DefaultGroup.ensure_for_user('dev-1')

        assert summary is not None
        assert summary.name == DEFAULT_GROUP_NAME

    async def test_it_arrives_holding_the_shipped_agents(self, client, member):
        headers, _ = member
        await DefaultGroup.ensure_for_user('dev-1')

        pack = (await client.get('/portal/me/group/pack', headers=headers)).json()
        got = {(o['kind'], o['entry_name']) for o in pack['entries']}
        assert got == {(kind, name) for kind, name, _ in seed_entries()}

    async def test_it_is_created_once(self, client, member):
        """A second one beside the group an admin has been editing would
        be worse than none."""
        headers, _ = member
        await DefaultGroup.ensure_for_user('dev-1')
        await DefaultGroup.ensure_for_user('dev-1')

        groups = (await client.get('/portal/groups', headers=headers)).json()['groups']
        assert [g['name'] for g in groups].count(DEFAULT_GROUP_NAME) == 1

    async def test_two_users_share_it(self, client, member, db):
        headers, org_id = member
        await create_user(db, user_id='dev-2', email='dev2@test.com')
        await add_member(db, org_id=org_id, user_id='dev-2', role='member')

        first = await DefaultGroup.ensure_for_user('dev-1')
        second = await DefaultGroup.ensure_for_user('dev-2')

        assert first.id == second.id

    async def test_someone_already_in_a_group_is_left_alone(self, client, member):
        """The admin put them there on purpose."""
        headers, _ = member
        legal = (await client.post('/portal/groups', json={'name': 'Legal'}, headers=headers)).json()
        await client.post(f'/portal/groups/{legal["id"]}/members', json={'user_id': 'dev-1'}, headers=headers)

        summary = await DefaultGroup.ensure_for_user('dev-1')

        assert summary.name == 'Legal'

    async def test_someone_in_no_org_gets_nothing(self, client, db):
        await create_user(db, user_id='nomad-1', email='nomad@test.com')

        assert await DefaultGroup.ensure_for_user('nomad-1') is None

    async def test_a_failure_costs_the_group_not_the_session(self, monkeypatch, client, member):
        """This runs while a CLI is starting up."""

        async def _boom(self, user_id):
            raise ConnectionError('database is down')

        # The *active* variant, which is what `ensure_for_user` calls
        # since B8 — patching the old one left this test proving that a
        # method no longer on the path can fail safely.
        monkeypatch.setattr('app.db.sql.groups.GroupsDB.get_active_user_group_summary', _boom)

        assert await DefaultGroup.ensure_for_user('dev-1') is None


class TestThroughThePack:
    async def test_asking_for_a_pack_is_what_triggers_it(self, client, member):
        """Lazily, so the orgs that already exist are covered as much as
        the ones made tomorrow."""
        headers, _ = member

        pack = (await client.get('/portal/me/group/pack', headers=headers)).json()

        assert pack is not None
        assert pack['group_name'] == DEFAULT_GROUP_NAME
        assert len(pack['entries']) == len(seed_entries())

    async def test_an_admin_edits_a_copy_of_it(self, client, member):
        """The point of the whole thing: what a team's assistant can do
        is edited in the portal, not shipped in a CLI release. Through a
        copy, because the standard set has to stay the standard set."""
        headers, _ = member
        default_id = await _default_id(client, headers)
        mine = (
            await client.post(
                '/portal/groups',
                json={'name': 'Engineering', 'template_group_id': default_id},
                headers=headers,
            )
        ).json()
        entries = (await client.get(f'/portal/groups/{mine["id"]}/entries', headers=headers)).json()['entries']
        reviewer = next(o for o in entries if o['entry_name'] == 'reviewer')

        resp = await client.delete(f'/portal/groups/{mine["id"]}/entries/{reviewer["id"]}', headers=headers)

        assert resp.status_code == 204
        after = (await client.get(f'/portal/groups/{mine["id"]}/entries', headers=headers)).json()
        assert 'reviewer' not in {o['entry_name'] for o in after['entries']}


async def _default_id(client, headers) -> str:
    groups = (await client.get('/portal/groups', headers=headers)).json()['groups']
    return next(g['id'] for g in groups if g['is_default'])


class TestItCannotBeChanged:
    """It is the copy of what igni ships and the thing every other
    group is started from. An org that edited it would have no reference
    point left and nothing to copy — so the way to have your own version
    is to make one.

    409 rather than 403 throughout: the admin is not lacking permission,
    the group is immutable, and the portal has to tell those apart to
    say anything useful.
    """

    async def test_it_cannot_be_renamed(self, client, member):
        headers, _ = member
        default_id = await _default_id(client, headers)

        resp = await client.patch(f'/portal/groups/{default_id}', json={'name': 'Engineering'}, headers=headers)

        assert resp.status_code == 409
        # The refusal has to say what to do instead.
        assert 'Create a group from it' in resp.json()['detail']

    async def test_its_description_cannot_be_changed(self, client, member):
        headers, _ = member
        default_id = await _default_id(client, headers)

        resp = await client.patch(f'/portal/groups/{default_id}', json={'description': 'mine now'}, headers=headers)

        assert resp.status_code == 409

    async def test_its_model_cannot_be_pinned(self, client, member):
        """Copy it and pin the model on the copy."""
        headers, _ = member
        default_id = await _default_id(client, headers)

        resp = await client.patch(
            f'/portal/groups/{default_id}', json={'default_model': 'legal-reviewer'}, headers=headers
        )

        assert resp.status_code == 409

    async def test_it_cannot_be_deleted(self, client, member):
        headers, _ = member
        default_id = await _default_id(client, headers)

        resp = await client.delete(f'/portal/groups/{default_id}', headers=headers)

        assert resp.status_code == 409

    async def test_nothing_can_be_added_to_it(self, client, member):
        headers, _ = member
        default_id = await _default_id(client, headers)

        resp = await client.post(
            f'/portal/groups/{default_id}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'mine',
                'content': '---\nname: mine\n---\nBody.',
                'content_type': 'markdown',
            },
            headers=headers,
        )

        assert resp.status_code == 409

    async def test_its_entries_cannot_be_edited(self, client, member):
        headers, _ = member
        default_id = await _default_id(client, headers)
        entries = (await client.get(f'/portal/groups/{default_id}/entries', headers=headers)).json()['entries']

        resp = await client.patch(
            f'/portal/groups/{default_id}/entries/{entries[0]["id"]}',
            json={'enabled': False},
            headers=headers,
        )

        assert resp.status_code == 409

    async def test_its_entries_cannot_be_deleted(self, client, member):
        headers, _ = member
        default_id = await _default_id(client, headers)
        entries = (await client.get(f'/portal/groups/{default_id}/entries', headers=headers)).json()['entries']

        resp = await client.delete(f'/portal/groups/{default_id}/entries/{entries[0]["id"]}', headers=headers)

        assert resp.status_code == 409

    async def test_but_people_can_still_be_put_in_it(self, client, member, db):
        """It is where anyone with no group lands, so membership is not
        part of what is frozen."""
        headers, org_id = member
        await create_user(db, user_id='dev-2', email='dev2@test.com')
        await add_member(db, org_id=org_id, user_id='dev-2', role='member')
        default_id = await _default_id(client, headers)

        resp = await client.post(f'/portal/groups/{default_id}/members', json={'user_id': 'dev-2'}, headers=headers)

        assert resp.status_code in (200, 201)

    async def test_and_a_copy_of_it_is_fully_editable(self, client, member):
        headers, _ = member
        default_id = await _default_id(client, headers)

        mine = (
            await client.post(
                '/portal/groups',
                json={'name': 'Engineering', 'template_group_id': default_id},
                headers=headers,
            )
        ).json()
        resp = await client.patch(f'/portal/groups/{mine["id"]}', json={'name': 'Platform'}, headers=headers)

        assert resp.status_code == 200
        assert resp.json()['is_default'] is False


class TestWhereAnAdminLooksForIt:
    """It used to appear only when somebody with no group asked for
    their pack. An org that already had groups — the one most likely to
    want the standard set as a template — never got it."""

    async def test_listing_groups_creates_it(self, client, member):
        headers, _ = member

        groups = (await client.get('/portal/groups', headers=headers)).json()['groups']

        assert DEFAULT_GROUP_NAME in {g['name'] for g in groups}

    async def test_it_arrives_holding_the_shipped_agents(self, client, member):
        headers, _ = member

        groups = (await client.get('/portal/groups', headers=headers)).json()['groups']
        default = next(g for g in groups if g['name'] == DEFAULT_GROUP_NAME)

        assert default['entry_count'] == len(seed_entries())

    async def test_an_org_that_already_has_groups_gets_it_too(self, client, member):
        """The case that was broken: nobody here is ungrouped, so the
        pack path would never have fired."""
        headers, _ = member
        legal = (await client.post('/portal/groups', json={'name': 'Legal'}, headers=headers)).json()
        await client.post(f'/portal/groups/{legal["id"]}/members', json={'user_id': 'dev-1'}, headers=headers)

        groups = (await client.get('/portal/groups', headers=headers)).json()['groups']

        assert {'Legal', DEFAULT_GROUP_NAME} <= {g['name'] for g in groups}

    async def test_listing_twice_does_not_make_two(self, client, member):
        headers, _ = member
        await client.get('/portal/groups', headers=headers)

        groups = (await client.get('/portal/groups', headers=headers)).json()['groups']

        assert [g['name'] for g in groups].count(DEFAULT_GROUP_NAME) == 1
