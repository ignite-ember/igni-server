"""What `GroupPackService` hands a CLI from the group its person is in.

The endpoint around it is pinned in `test_the_group_pack_is_signed.py`
and `test_portal_groups.py`. Here the service is asked directly, against
the real stores: which entries go in, what each carries, and the two
states that answer with no pack or with no theme rather than an error.
"""

from __future__ import annotations

import pytest

from app.db.sql.groups import GroupsDB
from app.db.sql.schemas.group import GroupPatch, GroupSummary
from app.services.portal.group_pack_service import GroupPackService
from tests.conftest import add_member, auth_header, create_org, create_user, make_token


@pytest.fixture
async def member_of_legal(client, db):
    """An admin in a group called Legal, with the headers to edit it."""
    await create_user(db, user_id='admin-1', email='admin@test.com')
    org = await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')
    org_id = str(org.id)
    await add_member(db, org_id=org_id, user_id='admin-1', role='admin')
    headers = auth_header(make_token('admin-1', org_id=org_id))
    group = (await client.post('/portal/groups', json={'name': 'Legal'}, headers=headers)).json()
    await client.post(f'/portal/groups/{group["id"]}/members', json={'user_id': 'admin-1'}, headers=headers)
    return headers, org_id, group['id']


async def _add(client, headers, group_id, **entry):
    resp = await client.post(f'/portal/groups/{group_id}/entries', json=entry, headers=headers)
    assert resp.status_code == 201, resp.text
    return next(o for o in resp.json()['entries'] if o['entry_name'] == entry['entry_name'])


class TestWhatGoesIn:
    async def test_a_disabled_entry_is_left_out(self, client, member_of_legal):
        """Switching an entry off is how an admin withdraws it without losing it."""
        headers, org_id, group_id = member_of_legal
        await _add(client, headers, group_id, kind='settings', entry_name='kept', content='{}', content_type='json')
        off = await _add(
            client, headers, group_id, kind='settings', entry_name='off', content='{}', content_type='json'
        )
        await client.patch(f'/portal/groups/{group_id}/entries/{off["id"]}', json={'enabled': False}, headers=headers)

        pack = await GroupPackService().get_my_group_pack(user_id='admin-1', org_id=org_id)

        assert pack is not None
        assert [entry.entry_name for entry in pack.entries] == ['kept']

    async def test_a_plugin_carries_where_to_install_it_from(self, client, member_of_legal):
        headers, org_id, group_id = member_of_legal
        await _add(
            client,
            headers,
            group_id,
            kind='plugins',
            entry_name='agentic-eval',
            content='name: agentic-eval',
            content_type='yaml',
            source_url='https://github.com/owner/repo',
            source_ref='main',
            source_subdir='plugins/agentic-eval',
        )

        pack = await GroupPackService().get_my_group_pack(user_id='admin-1', org_id=org_id)

        assert pack is not None
        (plugin,) = pack.entries
        assert (plugin.kind, plugin.entry_name, plugin.content, plugin.content_type, plugin.enabled) == (
            'plugins',
            'agentic-eval',
            'name: agentic-eval',
            'yaml',
            True,
        )
        assert (plugin.source_url, plugin.source_ref, plugin.source_subdir) == (
            'https://github.com/owner/repo',
            'main',
            'plugins/agentic-eval',
        )

    async def test_it_names_the_group_and_its_organisation(self, member_of_legal):
        _, org_id, group_id = member_of_legal

        pack = await GroupPackService().get_my_group_pack(user_id='admin-1', org_id=org_id)

        assert pack is not None
        assert (pack.group_id, pack.group_name, pack.org_id) == (group_id, 'Legal', org_id)


class TestWhatIsMissing:
    async def test_a_theme_that_cannot_be_read_serves_no_theme(self, member_of_legal):
        """A cosmetic column never takes the pack down for everyone in the group."""
        _, org_id, group_id = member_of_legal
        await GroupsDB().update_group(org_id, group_id=group_id, patch=GroupPatch(theme='{not a theme'))

        pack = await GroupPackService().get_my_group_pack(user_id='admin-1', org_id=org_id)

        assert pack is not None and pack.group_id == group_id
        assert pack.theme is None

    async def test_a_group_that_is_gone_gives_no_pack(self):
        """The membership names a group the store no longer has: no pack, not an error."""

        class _DefaultGroup:
            async def ensure_for_user(self, user_id, org_id=None):
                return GroupSummary(id='g-gone', org_id='org-1', name='Gone')

        class _Groups:
            async def get_group(self, org_id, *, group_id):
                return None

        class _Entries:
            async def list_entries(self, org_id, group_id):
                raise AssertionError('nothing to list for a group that is gone')

        service = GroupPackService(
            groups=_Groups(),  # type: ignore[arg-type]
            entries=_Entries(),  # type: ignore[arg-type]
            default_group=_DefaultGroup(),  # type: ignore[arg-type]
        )
        assert await service.get_my_group_pack(user_id='anyone', org_id='org-1') is None
