"""How many people and entries each group holds, read for a whole page at once.

The groups list used to read every member row and every entry row of
every group on the page, one group at a time, only to take `len()` of
them: 2N+2 round trips for N groups. `GroupsDB.tallies` answers the
same counts with two grouped queries in one transaction. The counts must
not change, and a group that holds nothing — absent from a `GROUP BY` —
must still answer, as zeros.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.base_v1.schema.portal.group_theme import GroupTheme
from app.api.base_v1.schema.portal.groups import (
    CopyGroupRequest,
    CreateGroupRequest,
    OrgGroupDetail,
    OrgGroupList,
)
from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.groups import GroupsDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.schemas.group import EntryCreate, GroupPage, GroupSettings, GroupTally
from app.db.sql.tenancy import TenantSessions
from app.services.portal.groups_service import GroupsService
from tests.conftest import add_member, auth_header, create_org, create_user, make_token

_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(name: str, *, enabled: bool = True) -> EntryCreate:
    return EntryCreate(
        kind='agents',
        entry_name=name,
        content=f'---\nname: {name}\n---\nBody.',
        content_type='markdown',
        enabled=enabled,
    )


@pytest.fixture
async def org_id(db) -> str:
    await create_user(db, user_id='admin-1', email='admin@test.com')
    return str((await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')).id)


async def _group(org_id: str, name: str) -> OrgGroup:
    return await GroupsDB().create_group(org_id, settings=GroupSettings(name=name), by_user_id='admin-1')


class TestTheStoreCounts:
    async def test_each_group_gets_its_own_counts(self, org_id):
        legal = await _group(org_id, 'Legal')
        sales = await _group(org_id, 'Sales')
        empty = await _group(org_id, 'Empty')
        for user_id in ('u-1', 'u-2'):
            await GroupMembersDB().place(org_id, group_id=legal.id, user_id=user_id, added_by_user_id='admin-1')
        await GroupMembersDB().place(org_id, group_id=sales.id, user_id='u-3', added_by_user_id='admin-1')
        for name in ('reviewer', 'writer', 'planner'):
            await GroupEntriesDB().create_entry(org_id, group_id=sales.id, entry=_entry(name), by_user_id='admin-1')

        tallies = await GroupsDB().tallies(org_id, group_ids=[legal.id, sales.id, empty.id])

        assert tallies == {
            legal.id: GroupTally(member_count=2, entry_count=0),
            sales.id: GroupTally(member_count=1, entry_count=3),
            empty.id: GroupTally(),
        }

    async def test_a_group_holding_nothing_is_all_zeros(self, org_id):
        empty = await _group(org_id, 'Empty')

        assert await GroupsDB().tallies(org_id, group_ids=[empty.id]) == {
            empty.id: GroupTally(member_count=0, entry_count=0)
        }

    async def test_another_organisations_group_counts_nothing_here(self, org_id, db):
        await create_user(db, user_id='admin-2', email='other@test.com')
        other = str((await create_org(db, owner_user_id='admin-2', slug='other', display_name='Other')).id)
        theirs = await GroupsDB().create_group(other, settings=GroupSettings(name='Theirs'), by_user_id='admin-2')
        await GroupMembersDB().place(other, group_id=theirs.id, user_id='u-9', added_by_user_id='admin-2')
        await GroupEntriesDB().create_entry(other, group_id=theirs.id, entry=_entry('reviewer'), by_user_id='admin-2')

        assert await GroupsDB().tallies(org_id, group_ids=[theirs.id]) == {theirs.id: GroupTally()}
        assert await GroupsDB().tallies(other, group_ids=[theirs.id]) == {
            theirs.id: GroupTally(member_count=1, entry_count=1)
        }

    async def test_a_group_with_entries_and_nobody_in_it_counts_its_entries(self, org_id):
        """Each count is filled on its own: no members must not zero the entries."""
        library = await _group(org_id, 'Library')
        await GroupEntriesDB().create_entry(org_id, group_id=library.id, entry=_entry('reviewer'), by_user_id='admin-1')

        assert await GroupsDB().tallies(org_id, group_ids=[library.id]) == {
            library.id: GroupTally(member_count=0, entry_count=1)
        }

    async def test_a_switched_off_entry_still_counts(self, org_id):
        """The count was the length of every entry the group holds, on or off."""
        legal = await _group(org_id, 'Legal')
        await GroupEntriesDB().create_entry(org_id, group_id=legal.id, entry=_entry('reviewer'), by_user_id='admin-1')
        await GroupEntriesDB().create_entry(
            org_id, group_id=legal.id, entry=_entry('writer', enabled=False), by_user_id='admin-1'
        )

        assert (await GroupsDB().tallies(org_id, group_ids=[legal.id]))[legal.id].entry_count == 2

    async def test_a_whole_page_is_counted_in_one_transaction(self, org_id):
        """One snapshot for the page, not a session per group or per table."""
        groups = [await _group(org_id, name) for name in ('Legal', 'Sales', 'Design')]
        await GroupMembersDB().place(org_id, group_id=groups[0].id, user_id='u-1', added_by_user_id='admin-1')
        await GroupEntriesDB().create_entry(
            org_id, group_id=groups[1].id, entry=_entry('reviewer'), by_user_id='admin-1'
        )

        class _Counted:
            def __init__(self) -> None:
                self.opened = 0

            def session_scope(self, org_id: str):
                self.opened += 1
                return TenantSessions().session_scope(org_id)

        sessions = _Counted()
        tallies = await GroupsDB(sessions=sessions).tallies(org_id, group_ids=[group.id for group in groups])

        assert sessions.opened == 1
        assert tallies == {
            groups[0].id: GroupTally(member_count=1),
            groups[1].id: GroupTally(entry_count=1),
            groups[2].id: GroupTally(),
        }

    async def test_no_groups_opens_no_session(self):
        class _NoSessions:
            def session_scope(self, org_id: str):
                raise AssertionError(f'opened a session on {org_id} for no groups')

        assert await GroupsDB(sessions=_NoSessions()).tallies('org-1', group_ids=[]) == {}


class TestEachTableCountsInTheCallersTransaction:
    async def test_only_the_groups_asked_for_and_only_those_holding_something(self, org_id):
        legal, sales, empty = [await _group(org_id, name) for name in ('Legal', 'Sales', 'Empty')]
        for user_id in ('u-1', 'u-2'):
            await GroupMembersDB().place(org_id, group_id=legal.id, user_id=user_id, added_by_user_id='admin-1')
        await GroupMembersDB().place(org_id, group_id=sales.id, user_id='u-3', added_by_user_id='admin-1')
        await GroupEntriesDB().create_entry(org_id, group_id=sales.id, entry=_entry('reviewer'), by_user_id='admin-1')

        async with TenantSessions().session_scope(org_id) as session:
            members = await GroupMembersDB().count_by_group(session, group_ids=[legal.id, empty.id])
            entries = await GroupEntriesDB().count_by_group(session, group_ids=[legal.id, sales.id, empty.id])

        assert members == {legal.id: 2}
        assert entries == {sales.id: 1}

    async def test_no_ids_count_nothing(self, org_id):
        await GroupMembersDB().place(
            org_id, group_id=(await _group(org_id, 'Legal')).id, user_id='u-1', added_by_user_id='admin-1'
        )

        async with TenantSessions().session_scope(org_id) as session:
            assert await GroupMembersDB().count_by_group(session, group_ids=[]) == {}
            assert await GroupEntriesDB().count_by_group(session, group_ids=[]) == {}


class TestTheListingOverHttp:
    async def test_a_later_page_counts_its_own_groups_and_the_total_counts_them_all(self, client, db, org_id):
        await add_member(db, org_id=org_id, user_id='admin-1', role='admin')
        headers = auth_header(make_token('admin-1', org_id=org_id))
        await client.get('/portal/groups', headers=headers)  # Default, first, holding the admin
        legal = (await client.post('/portal/groups', json={'name': 'Legal'}, headers=headers)).json()
        await client.post('/portal/groups', json={'name': 'Sales'}, headers=headers)
        await client.post(f'/portal/groups/{legal["id"]}/members', json={'user_id': 'admin-1'}, headers=headers)

        resp = await client.get('/portal/groups', params={'offset': 1, 'limit': 1}, headers=headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body['total'], body['offset'], body['limit']) == (3, 1, 1)
        assert [(g['name'], g['member_count'], g['entry_count']) for g in body['groups']] == [('Legal', 1, 0)]


def _stored(group_id: str, name: str, *, is_default: bool = False) -> OrgGroup:
    return OrgGroup(
        id=group_id,
        org_id='org-1',
        name=name,
        description=None,
        default_model=None,
        theme=None,
        is_default=is_default,
        created_by_user_id='admin-1',
        created_at=_AT,
        updated_at=_AT,
    )


class _Groups:
    """A `GroupsDB` holding one page, recording how often it is asked for counts."""

    def __init__(self, page: list[OrgGroup], tallies: dict[str, GroupTally]) -> None:
        self._page = page
        self._tallies = tallies
        self.tally_calls: list[list[str]] = []

    async def list_groups(self, org_id: str, *, offset: int = 0, limit: int | None = None) -> GroupPage:
        return GroupPage(items=self._page, total=len(self._page))

    async def tallies(self, org_id: str, *, group_ids: list[str]) -> dict[str, GroupTally]:
        self.tally_calls.append(group_ids)
        return {group_id: self._tallies[group_id] for group_id in group_ids}


class _DefaultGroup:
    def __init__(self) -> None:
        self.placed: list[tuple[str, str]] = []

    async def place_everyone(self, org_id: str, *, by_user_id: str) -> None:
        self.placed.append((org_id, by_user_id))


class TestTheServiceListsAPage:
    async def test_one_count_for_the_whole_page_after_everybody_is_placed(self):
        default, legal = _stored('g-1', 'Default', is_default=True), _stored('g-2', 'Legal')
        groups = _Groups(
            [default, legal],
            {'g-1': GroupTally(member_count=4, entry_count=12), 'g-2': GroupTally(member_count=1)},
        )
        placement = _DefaultGroup()
        service = GroupsService(groups=groups, default_group=placement, served=object())

        listed = await service.list_groups(org_id='org-1', by_user_id='admin-1', limit=10)

        assert placement.placed == [('org-1', 'admin-1')]
        assert groups.tally_calls == [['g-1', 'g-2']]
        assert listed == OrgGroupList(
            groups=[
                OrgGroupDetail.of(default, GroupTally(member_count=4, entry_count=12)),
                OrgGroupDetail.of(legal, GroupTally(member_count=1, entry_count=0)),
            ],
            total=2,
            offset=0,
            limit=10,
        )
        assert listed.model_dump(mode='json')['groups'][0] == {
            'id': 'g-1',
            'org_id': 'org-1',
            'name': 'Default',
            'description': None,
            'default_model': None,
            'theme': None,
            'is_default': True,
            'created_by_user_id': 'admin-1',
            'created_at': '2026-01-01T00:00:00Z',
            'updated_at': '2026-01-01T00:00:00Z',
            'member_count': 4,
            'entry_count': 12,
        }


class TestARequestBecomesSettings:
    def test_a_group_nobody_counted_shows_zeros(self):
        detail = OrgGroupDetail.of(_stored('g-1', 'Legal'))

        assert (detail.member_count, detail.entry_count) == (0, 0)

    def test_a_new_group_takes_what_the_request_says(self):
        body = CreateGroupRequest(
            name='Legal', description='Contracts', default_model='lora-legal', theme=GroupTheme(accent='#112233')
        )

        assert body.to_settings() == GroupSettings(
            name='Legal',
            description='Contracts',
            default_model='lora-legal',
            theme=GroupTheme(accent='#112233').to_column(),
        )

    def test_an_empty_model_and_a_theme_overriding_nothing_are_none(self):
        body = CreateGroupRequest(name='Legal', default_model='', theme=GroupTheme())

        assert body.to_settings() == GroupSettings(name='Legal', description=None, default_model=None, theme=None)

    def test_a_copy_keeps_the_sources_settings_under_its_own_name(self):
        source = _stored('g-1', 'Legal')
        source.description, source.default_model, source.theme = 'Contracts', 'lora-legal', '{"accent": "#112233"}'

        assert CopyGroupRequest(name='Legal 2').to_settings(source) == GroupSettings(
            name='Legal 2', description='Contracts', default_model='lora-legal', theme='{"accent": "#112233"}'
        )

    def test_a_copys_description_replaces_the_sources_even_when_empty(self):
        source = _stored('g-1', 'Legal')
        source.description = 'Contracts'

        assert CopyGroupRequest(name='Legal 2', description='').to_settings(source).description == ''
        assert CopyGroupRequest(name='Legal 2', description='Briefs').to_settings(source).description == 'Briefs'
