"""Integration tests for portal group endpoints."""

from datetime import datetime, timezone

from app.api.base_v1.schema.portal.group_pack import OrgGroupPack
from tests.conftest import add_member, auth_header, create_org, create_user, make_token


class TestListGroups:
    async def test_a_fresh_org_gets_the_default_group(self, client, db):
        """This used to answer an empty list. Listing an org's groups now
        seeds the one holding what igni ships — see
        tests/test_default_group.py for why that is where it happens."""
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        resp = await client.get('/portal/groups', headers=auth_header(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data['total'] == 1
        assert data['groups'][0]['is_default'] is True

    async def test_list_groups_as_member(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        await add_member(db, org_id=str(org.id), user_id='u1', role='admin')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')

        token = make_token('u2', org_id=str(org.id))
        resp = await client.get('/portal/groups', headers=auth_header(token))
        assert resp.status_code == 200

    async def test_list_groups_unauthenticated(self, client):
        resp = await client.get('/portal/groups')
        assert resp.status_code == 401


class TestCreateGroup:
    async def test_create_group(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        resp = await client.post(
            '/portal/groups',
            json={'name': 'Engineering', 'description': 'The eng team'},
            headers=auth_header(token),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data['name'] == 'Engineering'
        assert data['description'] == 'The eng team'
        assert data['org_id'] == str(org.id)

    async def test_create_duplicate_name_returns_409(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        await client.post('/portal/groups', json={'name': 'Eng'}, headers=auth_header(token))
        resp = await client.post(
            '/portal/groups',
            json={'name': 'Eng'},
            headers=auth_header(token),
        )
        assert resp.status_code == 409

    async def test_create_group_non_admin_forbidden(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        await add_member(db, org_id=str(org.id), user_id='u1', role='admin')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')

        token = make_token('u2', org_id=str(org.id))
        resp = await client.post(
            '/portal/groups',
            json={'name': 'Contractors'},
            headers=auth_header(token),
        )
        assert resp.status_code == 403


class TestUpdateGroup:
    async def test_update_group_name(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        g = await client.post(
            '/portal/groups',
            json={'name': 'Old Name'},
            headers=auth_header(token),
        )
        group_id = g.json()['id']

        resp = await client.patch(
            f'/portal/groups/{group_id}',
            json={'name': 'New Name'},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        assert resp.json()['name'] == 'New Name'

    async def test_delete_group(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        g = await client.post('/portal/groups', json={'name': 'ToDelete'}, headers=auth_header(token))
        group_id = g.json()['id']

        resp = await client.delete(f'/portal/groups/{group_id}', headers=auth_header(token))
        assert resp.status_code == 204

        # Verify gone
        list_resp = await client.get('/portal/groups', headers=auth_header(token))
        assert all(group['id'] != group_id for group in list_resp.json()['groups'])


class TestGroupMembers:
    async def test_add_member(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        await add_member(db, org_id=str(org.id), user_id='u1', role='admin')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')
        token = make_token('u1', org_id=str(org.id))

        g = await client.post('/portal/groups', json={'name': 'Eng'}, headers=auth_header(token))
        group_id = g.json()['id']

        resp = await client.post(
            f'/portal/groups/{group_id}/members',
            json={'user_id': 'u2'},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        members = resp.json()['members']
        assert any(m['user_id'] == 'u2' for m in members)

    async def test_adding_somebody_in_another_group_moves_them(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        await add_member(db, org_id=str(org.id), user_id='u1', role='admin')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')
        token = make_token('u1', org_id=str(org.id))

        g1 = await client.post('/portal/groups', json={'name': 'GroupA'}, headers=auth_header(token))
        g2 = await client.post('/portal/groups', json={'name': 'GroupB'}, headers=auth_header(token))

        await client.post(
            f'/portal/groups/{g1.json()["id"]}/members',
            json={'user_id': 'u2'},
            headers=auth_header(token),
        )
        resp = await client.post(
            f'/portal/groups/{g2.json()["id"]}/members',
            json={'user_id': 'u2'},
            headers=auth_header(token),
        )
        assert resp.status_code == 200
        assert [m['user_id'] for m in resp.json()['members']] == ['u2']

        left = await client.get(f'/portal/groups/{g1.json()["id"]}/members', headers=auth_header(token))
        assert left.json()['members'] == []

    async def test_remove_member(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        await add_member(db, org_id=str(org.id), user_id='u1', role='admin')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')
        token = make_token('u1', org_id=str(org.id))

        g = await client.post('/portal/groups', json={'name': 'Eng'}, headers=auth_header(token))
        group_id = g.json()['id']
        await client.post(
            f'/portal/groups/{group_id}/members',
            json={'user_id': 'u2'},
            headers=auth_header(token),
        )

        resp = await client.delete(
            f'/portal/groups/{group_id}/members/u2',
            headers=auth_header(token),
        )
        assert resp.status_code == 204

        back = await client.get('/portal/users/u2/group', headers=auth_header(token))
        assert back.json()['name'] == 'Default'

    async def test_nobody_is_removed_from_the_default_group(self, client, db):
        """There is nowhere further back to put them; they leave it only by being moved."""
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        await add_member(db, org_id=str(org.id), user_id='u1', role='admin')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')
        headers = auth_header(make_token('u1', org_id=str(org.id)))
        groups = (await client.get('/portal/groups', headers=headers)).json()['groups']
        default_id = next(g['id'] for g in groups if g['is_default'])

        resp = await client.delete(f'/portal/groups/{default_id}/members/u2', headers=headers)

        assert resp.status_code == 409
        assert 'moved' in resp.json()['detail']

    async def test_everybody_is_in_a_group_once_anyone_looks(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        await add_member(db, org_id=str(org.id), user_id='u1', role='admin')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')
        headers = auth_header(make_token('u1', org_id=str(org.id)))

        resp = await client.get('/portal/group-memberships', headers=headers)

        assert resp.status_code == 200, resp.text
        memberships = resp.json()['memberships']
        assert sorted(m['user_id'] for m in memberships) == ['u1', 'u2']
        assert {m['group_name'] for m in memberships} == {'Default'}
        assert all(m['is_default'] for m in memberships)

    async def test_deleting_a_group_puts_its_people_back_in_default(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        await add_member(db, org_id=str(org.id), user_id='u1', role='admin')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')
        headers = auth_header(make_token('u1', org_id=str(org.id)))
        group_id = (await client.post('/portal/groups', json={'name': 'Eng'}, headers=headers)).json()['id']
        await client.post(f'/portal/groups/{group_id}/members', json={'user_id': 'u2'}, headers=headers)

        assert (await client.delete(f'/portal/groups/{group_id}', headers=headers)).status_code == 204

        back = await client.get('/portal/users/u2/group', headers=headers)
        assert back.json()['name'] == 'Default'

    async def test_get_user_group(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        g = await client.post('/portal/groups', json={'name': 'Eng'}, headers=auth_header(token))
        group_id = g.json()['id']
        await client.post(
            f'/portal/groups/{group_id}/members',
            json={'user_id': 'u1'},
            headers=auth_header(token),
        )

        resp = await client.get('/portal/users/u1/group', headers=auth_header(token))
        assert resp.status_code == 200
        assert resp.json()['id'] == group_id


class TestGroupEntrys:
    async def test_create_entry(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        g = await client.post('/portal/groups', json={'name': 'Eng'}, headers=auth_header(token))
        group_id = g.json()['id']

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'code-reviewer',
                'content': '# Code Reviewer\nA thorough code reviewer.',
                'content_type': 'markdown',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 201
        entries = resp.json()['entries']
        assert any(o['entry_name'] == 'code-reviewer' for o in entries)

    async def test_create_duplicate_entry_returns_409(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        g = await client.post('/portal/groups', json={'name': 'Eng'}, headers=auth_header(token))
        group_id = g.json()['id']

        await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'swe-agent',
                'content': '# SWE Agent',
                'content_type': 'markdown',
            },
            headers=auth_header(token),
        )
        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'swe-agent',
                'content': '# Different Agent',
                'content_type': 'markdown',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 409

    async def test_update_entry_enabled(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        g = await client.post('/portal/groups', json={'name': 'Eng'}, headers=auth_header(token))
        group_id = g.json()['id']

        await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'settings',
                'entry_name': 'safety',
                'content': '{"auto_approve": false}',
                'content_type': 'json',
            },
            headers=auth_header(token),
        )
        list_resp = await client.get(
            f'/portal/groups/{group_id}/entries',
            headers=auth_header(token),
        )
        entry_id = list_resp.json()['entries'][0]['id']

        patch_resp = await client.patch(
            f'/portal/groups/{group_id}/entries/{entry_id}',
            json={'enabled': False},
            headers=auth_header(token),
        )
        updated = patch_resp.json()['entries']
        assert any(o['id'] == entry_id and o['enabled'] is False for o in updated)

    async def test_delete_entry(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        g = await client.post('/portal/groups', json={'name': 'Eng'}, headers=auth_header(token))
        group_id = g.json()['id']

        await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'temp-agent',
                'content': '# Temp',
                'content_type': 'markdown',
            },
            headers=auth_header(token),
        )
        list_resp = await client.get(
            f'/portal/groups/{group_id}/entries',
            headers=auth_header(token),
        )
        entry_id = list_resp.json()['entries'][0]['id']

        del_resp = await client.delete(
            f'/portal/groups/{group_id}/entries/{entry_id}',
            headers=auth_header(token),
        )
        assert del_resp.status_code == 204


class TestPluginSourceEntry:
    """POST /portal/groups/{id}/entries must accept plugin-source
    fields (URL/ref/subdir) and validate them, matching the FE
    guard rails and the igni group's cross-field validator.
    """

    async def _make_group(self, client, db) -> tuple[str, str]:
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))
        g = await client.post('/portal/groups', json={'name': 'Plugins'}, headers=auth_header(token))
        return token, g.json()['id']

    async def test_accepts_full_source_url_triplet(self, client, db):
        """Round-trip a plugin entry with all three source fields."""
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'agentic-eval',
                'content': 'name: agentic-eval',
                'content_type': 'yaml',
                'source_url': 'https://github.com/owner/repo',
                'source_ref': 'main',
                'source_subdir': 'plugins/agentic-eval',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 201
        entry = next(o for o in resp.json()['entries'] if o['entry_name'] == 'agentic-eval')
        assert entry['source_url'] == 'https://github.com/owner/repo'
        assert entry['source_ref'] == 'main'
        assert entry['source_subdir'] == 'plugins/agentic-eval'

    async def test_accepts_source_url_without_content(self, client, db):
        """A plugin with a source_url doesn't need legacy YAML content."""
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'remote-only',
                'content': '',
                'content_type': 'yaml',
                'source_url': 'https://github.com/owner/repo',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 201
        entry = next(o for o in resp.json()['entries'] if o['entry_name'] == 'remote-only')
        assert entry['source_url'] == 'https://github.com/owner/repo'

    async def test_rejects_ref_without_url(self, client, db):
        """Half-filled source specs fail the cross-field validator."""
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'halfref',
                'content': '',
                'content_type': 'yaml',
                'source_ref': 'main',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 422
        body = resp.text
        assert 'source_url' in body
        assert 'source_ref' in body or 'source_subdir' in body

    async def test_rejects_subdir_without_url(self, client, db):
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'halfsubdir',
                'content': '',
                'content_type': 'yaml',
                'source_subdir': 'plugins/x',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 422

    async def test_rejects_unsupported_url_scheme(self, client, db):
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'bad-scheme',
                'content': '',
                'content_type': 'yaml',
                'source_url': 'file:///etc/passwd',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 422
        # Service-layer URL-shape message, distinct from the
        # Pydantic cross-field validator message.
        assert 'unsupported scheme' in resp.text or 'http(s), git, or ssh' in resp.text

    async def test_rejects_malformed_url(self, client, db):
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'malformed',
                'content': '',
                'content_type': 'yaml',
                'source_url': 'not a url at all',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 422

    async def test_accepts_legacy_plugin_entry_without_source(self, client, db):
        """Back-compat: pre-source URLs entry rows should still POST OK."""
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'legacy',
                'content': 'name: legacy',
                'content_type': 'yaml',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 201
        entry = next(o for o in resp.json()['entries'] if o['entry_name'] == 'legacy')
        assert entry['source_url'] is None
        assert entry['source_ref'] is None
        assert entry['source_subdir'] is None

    async def test_list_entries_returns_source_fields(self, client, db):
        """The GET response includes source_url / source_ref / source_subdir."""
        token, group_id = await self._make_group(client, db)

        await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'roundtrip',
                'content': '',
                'content_type': 'yaml',
                'source_url': 'https://example.com/repo',
                'source_ref': 'v1.0',
            },
            headers=auth_header(token),
        )
        list_resp = await client.get(
            f'/portal/groups/{group_id}/entries',
            headers=auth_header(token),
        )
        assert list_resp.status_code == 200
        entry = next(o for o in list_resp.json()['entries'] if o['entry_name'] == 'roundtrip')
        assert entry['source_url'] == 'https://example.com/repo'
        assert entry['source_ref'] == 'v1.0'
        assert entry['source_subdir'] is None

    async def test_trims_whitespace_on_source_fields(self, client, db):
        """Whitespace around URL/ref/subdir is stripped before storage."""
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'trimmed',
                'content': '',
                'content_type': 'yaml',
                'source_url': '  https://github.com/owner/repo  ',
                'source_ref': '  main  ',
                'source_subdir': '  plugins/x  ',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 201
        entry = next(o for o in resp.json()['entries'] if o['entry_name'] == 'trimmed')
        assert entry['source_url'] == 'https://github.com/owner/repo'
        assert entry['source_ref'] == 'main'
        assert entry['source_subdir'] == 'plugins/x'

    async def test_empty_string_url_with_ref_is_rejected(self, client, db):
        """Empty strings are normalized to None server-side; a ref with
        an empty URL must still be rejected by the cross-field validator.
        """
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'empty-url',
                'content': '',
                'content_type': 'yaml',
                'source_url': '',
                'source_ref': 'main',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 422

    async def test_non_plugin_kinds_ignore_source_fields(self, client, db):
        """Agents/mcps/settings POSTs without source fields work and
        lists return source_url/source_ref/source_subdir = None.
        """
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'plain-agent',
                'content': '# agent',
                'content_type': 'markdown',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 201
        entry = next(o for o in resp.json()['entries'] if o['entry_name'] == 'plain-agent')
        assert entry['source_url'] is None
        assert entry['source_ref'] is None
        assert entry['source_subdir'] is None

    async def test_https_url_accepted(self, client, db):
        """https:// is allowed."""
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'https-plugin',
                'content': '',
                'content_type': 'yaml',
                'source_url': 'https://example.com/r.git',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 201

    async def test_http_url_accepted(self, client, db):
        """http:// (insecure) is allowed at the wire — TLS enforcement
        is a deployment concern, not a per-row one.
        """
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'http-plugin',
                'content': '',
                'content_type': 'yaml',
                'source_url': 'http://example.com/r.git',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 201

    async def test_git_scheme_url_accepted(self, client, db):
        """``git://`` URLs are allowed (cloneable by GitPluginInstaller)."""
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'git-plugin',
                'content': '',
                'content_type': 'yaml',
                'source_url': 'git://example.com/repo.git',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 201

    async def test_ssh_scheme_url_accepted(self, client, db):
        """``ssh://`` URLs are allowed."""
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'ssh-plugin',
                'content': '',
                'content_type': 'yaml',
                'source_url': 'ssh://git@example.com/repo.git',
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 201

    async def test_max_length_enforced(self, client, db):
        """The source_url field has a max_length cap that mirrors the BE
        422 contract when exceeded.
        """
        token, group_id = await self._make_group(client, db)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'plugins',
                'entry_name': 'toolong',
                'content': '',
                'content_type': 'yaml',
                'source_url': 'https://x.com/' + ('a' * 2100),
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 422


class TestCLIPack:
    async def test_a_user_in_no_group_is_given_the_default_one(self, client, db):
        """This used to answer null. A user with no group now lands in
        their org's default group, seeded with the shipped agents — see
        tests/test_default_group.py for why."""
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        resp = await client.get('/portal/me/group/pack', headers=auth_header(token))
        assert resp.status_code == 200
        assert resp.json()['group_name'] == 'Default'

    async def test_an_account_in_no_organisation_gets_no_pack_and_no_error(self, client, db):
        """igni asks for the pack on every session. An account starts in
        no organisation now — portal auth no longer makes one up — and
        that is a normal state with nothing to hand out, not a refusal
        for the CLI to report."""
        await create_user(db, user_id='nomad', email='nomad@test.com')
        token = make_token('nomad', org_id=None)

        resp = await client.get('/portal/me/group/pack', headers=auth_header(token))
        assert resp.status_code == 200
        # "No pack", signed so igni can clear what it cached; no pack fields.
        body = resp.json()
        assert body['no_pack'] is True and 'entries' not in body

    async def test_get_my_group_pack_returns_entries(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        token = make_token('u1', org_id=str(org.id))

        g = await client.post('/portal/groups', json={'name': 'Eng'}, headers=auth_header(token))
        group_id = g.json()['id']
        await client.post(
            f'/portal/groups/{group_id}/members',
            json={'user_id': 'u1'},
            headers=auth_header(token),
        )
        await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'reviewer',
                'content': '# Reviewer Agent',
                'content_type': 'markdown',
            },
            headers=auth_header(token),
        )

        resp = await client.get('/portal/me/group/pack', headers=auth_header(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data is not None
        assert data['group_id'] == group_id
        assert data['group_name'] == 'Eng'
        assert len(data['entries']) == 1
        assert data['entries'][0]['entry_name'] == 'reviewer'

    async def test_members_of_an_org_share_its_default_group(self, client, db):
        """Both arrive in the same group rather than one each — it is the
        org's, not the user's."""
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        await add_member(db, org_id=str(org.id), user_id='u1', role='admin')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')

        first = (
            await client.get('/portal/me/group/pack', headers=auth_header(make_token('u1', org_id=str(org.id))))
        ).json()
        second = (
            await client.get('/portal/me/group/pack', headers=auth_header(make_token('u2', org_id=str(org.id))))
        ).json()

        assert first['group_id'] == second['group_id']


class TestThePackRevalidates:
    """igni polls this from every running session every few minutes, and
    the answer is almost always "same as before". A full pack of agent
    prompts each time would be a lot of bytes to say nothing."""

    async def _headers(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='test', display_name='Test Org')
        return auth_header(make_token('u1', org_id=str(org.id)))

    async def test_it_offers_an_etag(self, client, db):
        headers = await self._headers(client, db)

        resp = await client.get('/portal/me/group/pack', headers=headers)

        assert resp.headers.get('etag')

    async def test_the_etag_is_the_same_next_time(self, client, db):
        """It used to hash ``fetched_at``, which is now() — so it was
        present, well-formed, and could never match."""
        headers = await self._headers(client, db)

        first = await client.get('/portal/me/group/pack', headers=headers)
        second = await client.get('/portal/me/group/pack', headers=headers)

        assert first.headers['etag'] == second.headers['etag']

    async def test_sending_it_back_gets_a_304_and_no_body(self, client, db):
        headers = await self._headers(client, db)
        first = await client.get('/portal/me/group/pack', headers=headers)

        second = await client.get(
            '/portal/me/group/pack',
            headers={**headers, 'If-None-Match': first.headers['etag']},
        )

        assert second.status_code == 304
        assert not second.content

    async def test_editing_an_entry_changes_it(self, client, db):
        """Otherwise a client that trusts the tag would never see the
        edit — which is worse than not offering a tag at all."""
        headers = await self._headers(client, db)
        # The Default group is read-only, so move the user into a copy
        # of it that can be edited. A person belongs to one group, so
        # this is a move rather than a second membership.
        groups = (await client.get('/portal/groups', headers=headers)).json()['groups']
        default_id = next(g['id'] for g in groups if g['is_default'])
        mine = (
            await client.post(
                '/portal/groups',
                json={'name': 'Mine', 'template_group_id': default_id},
                headers=headers,
            )
        ).json()
        await client.post(f'/portal/groups/{mine["id"]}/members', json={'user_id': 'u1'}, headers=headers)

        first = await client.get('/portal/me/group/pack', headers=headers)
        entries = (await client.get(f'/portal/groups/{mine["id"]}/entries', headers=headers)).json()
        await client.delete(f'/portal/groups/{mine["id"]}/entries/{entries["entries"][0]["id"]}', headers=headers)

        second = await client.get('/portal/me/group/pack', headers=headers)

        assert second.headers['etag'] != first.headers['etag']

    async def test_a_moved_user_gets_a_different_tag(self, client, db):
        """Moving somebody between groups is the change most worth
        noticing quickly."""
        headers = await self._headers(client, db)
        first = await client.get('/portal/me/group/pack', headers=headers)

        empty = (await client.post('/portal/groups', json={'name': 'Legal'}, headers=headers)).json()
        await client.post(f'/portal/groups/{empty["id"]}/members', json={'user_id': 'u1'}, headers=headers)

        second = await client.get('/portal/me/group/pack', headers=headers)

        assert second.headers['etag'] != first.headers['etag']


class TestThePackKnowsItsOwnIdentity:
    """`as_payload` and `etag` moved off the handler onto `OrgGroupPack`.

    The endpoint tests above cover the behaviour — a stable tag, a 304,
    a new tag when the content changes. This covers the invariant the
    handler used to state in a comment and nothing enforced: a tag that
    included `fetched_at` would be well-formed, present, and unable to
    match by construction, so every poll would download the whole pack
    and every one of those tests would still pass.
    """

    def _pack(self, **overrides):
        fields = {
            'group_id': 'g-1',
            'group_name': 'Engineering',
            'fetched_at': datetime(2026, 1, 1, tzinfo=timezone.utc),
            'entries': [],
            'default_model': None,
        }
        return OrgGroupPack(**{**fields, **overrides})

    def test_the_tag_ignores_when_it_was_read(self):
        from datetime import datetime, timezone

        early = self._pack()
        later = self._pack(fetched_at=datetime(2027, 6, 5, tzinfo=timezone.utc))

        assert early.etag() == later.etag(), 'the tag changes with the clock, so it can never match'

    def test_the_tag_changes_when_the_content_does(self):
        """The other half: a tag that ignored everything would also be
        stable, and wrong in the dangerous direction."""
        assert self._pack().etag() != self._pack(group_name='Design').etag()

    def test_the_tag_changes_with_whom_it_is_bound_to(self):
        """A 304 must not hand one person, or one signing key, another's signature."""
        pack = self._pack()

        assert pack.etag(bound_to='alice') == self._pack().etag(bound_to='alice')
        assert len({pack.etag(), pack.etag(bound_to='alice'), pack.etag(bound_to='bob')}) == 3

    def test_the_organisation_stays_out_of_the_payload_and_the_tag(self):
        """Carried for the signed form only; the served pack and its tag are what they were."""
        named = self._pack(org_id='org-1')

        assert 'org_id' not in named.as_payload()
        assert named.etag() == self._pack().etag()

    def test_the_payload_is_json_ready(self):
        """`mode='json'`. Python mode hands `JSONResponse`'s encoder a
        raw `datetime`, which it refuses."""
        import json

        payload = self._pack().as_payload()

        assert isinstance(payload['fetched_at'], str)
        json.dumps(payload)
