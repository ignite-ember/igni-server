"""What the group aggregate reads, and how it forgets a person, through one `GroupTransaction`.

`GroupsDB` used to open its own sessions and run its own queries. The
queries moved into `GroupTransaction`, clearing a deleted account moved
to `GroupPersonErasureDB`, and the store stopped being a singleton.
These pin what that move had to keep: the listing is oldest first with
its paging rules, a lookup only answers for the organisation asked
about, a copy is stamped with one moment, a write that fails for any
reason other than the name says so, and forgetting a person is all of
it or none of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.group_extensions import GroupExtensionsDB
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.group_person_erasure import GroupPersonErasureDB
from app.db.sql.group_transaction import GroupTransaction
from app.db.sql.groups import GroupsDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_group_entry import OrgGroupEntry
from app.db.sql.models.org_group_extension import OrgGroupExtension
from app.db.sql.models.org_group_member import OrgGroupMember
from app.db.sql.schemas.group import EntryCreate, GroupNameTaken, GroupSettings
from app.db.sql.tenancy import TenantScoped, TenantSessions
from tests.conftest import create_org, create_user

_START = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

_AGENT = EntryCreate(
    kind='agents',
    entry_name='reviewer',
    content='---\nname: reviewer\n---\nBody.',
    content_type='markdown',
    enabled=True,
)


@pytest.fixture
async def org_id(db) -> str:
    await create_user(db, user_id='admin-1', email='admin@test.com')
    return str((await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')).id)


def _ticking() -> Iterator[datetime]:
    """A clock that moves a minute every time it is read."""
    at = _START
    while True:
        yield at
        at += timedelta(minutes=1)


async def _groups_made_in_order(org_id: str, *names: str) -> GroupsDB:
    ticks = _ticking()
    groups = GroupsDB(clock=lambda: next(ticks))
    for name in names:
        await groups.create_group(org_id, settings=GroupSettings(name=name), by_user_id='admin-1')
    return groups


async def _rows(org_id: str, model, *where):
    async with TenantSessions().session_scope(org_id) as session:
        return list((await session.execute(select(model).where(*where))).scalars().all())


async def _a_stray_row_for_another_org(org_id: str) -> None:
    """A row in this organisation's database that says it is somebody else's."""
    async with TenantSessions().session_scope(org_id) as session:
        session.add(
            GroupSettings(name='Stray').new_group(
                org_id='another-org', by_user_id='admin-1', is_default=False, vm_profile_id=None, at=_START
            )
        )


class TestTheListing:
    async def test_it_is_oldest_first_whatever_the_names(self, org_id):
        groups = await _groups_made_in_order(org_id, 'Zeta', 'Alpha', 'Mid')

        page = await groups.list_groups(org_id)

        assert [g.name for g in page.items] == ['Zeta', 'Alpha', 'Mid']
        assert page.total == 3

    async def test_a_page_is_cut_from_the_oldest_and_the_total_is_every_group(self, org_id):
        groups = await _groups_made_in_order(org_id, 'One', 'Two', 'Three', 'Four')

        page = await groups.list_groups(org_id, offset=1, limit=2)

        assert ([g.name for g in page.items], page.total) == (['Two', 'Three'], 4)

    async def test_a_limit_of_zero_is_every_group_not_none(self, org_id):
        groups = await _groups_made_in_order(org_id, 'One', 'Two', 'Three')

        page = await groups.list_groups(org_id, offset=0, limit=0)

        assert [g.name for g in page.items] == ['One', 'Two', 'Three']

    async def test_only_the_organisations_own_rows_are_listed_or_counted(self, org_id):
        groups = await _groups_made_in_order(org_id, 'Legal')
        await _a_stray_row_for_another_org(org_id)

        page = await groups.list_groups(org_id)

        assert ([g.name for g in page.items], page.total) == (['Legal'], 1)


class TestALookup:
    async def test_finds_a_group_by_id_or_by_name(self, org_id):
        legal = await GroupsDB().create_group(org_id, settings=GroupSettings(name='Legal'), by_user_id='admin-1')

        by_id = await GroupsDB().get_group(org_id, group_id=legal.id)
        by_name = await GroupsDB().get_group_by_name(org_id, name='Legal')

        assert by_id is not None and by_name is not None
        assert (by_id.id, by_name.id) == (legal.id, legal.id)
        assert await GroupsDB().get_group_by_name(org_id, name='legal') is None

    async def test_answers_nothing_for_a_row_that_is_another_organisations(self, org_id):
        await _a_stray_row_for_another_org(org_id)
        (stray,) = await _rows(org_id, OrgGroup)

        assert await GroupsDB().get_group(org_id, group_id=stray.id) is None
        assert await GroupsDB().get_group_by_name(org_id, name='Stray') is None


class TestWritingAGroup:
    async def test_a_new_group_has_no_vm_profile_of_its_own(self, org_id):
        group = await GroupsDB().create_group(
            org_id, settings=GroupSettings(name='Legal'), by_user_id='admin-1', is_default=True
        )

        (row,) = await _rows(org_id, OrgGroup, OrgGroup.id == group.id)
        assert (row.vm_profile_id, row.is_default, row.created_by_user_id) == (None, True, 'admin-1')

    async def test_a_failure_that_is_not_the_name_is_itself(self, org_id):
        """Only the name's unique constraint is `GroupNameTaken`; a missing VM profile is not a taken name."""
        row = GroupSettings(name='Legal').new_group(
            org_id=org_id, by_user_id='admin-1', is_default=False, vm_profile_id='no-such-profile', at=_START
        )

        with pytest.raises(IntegrityError) as raised:
            async with TenantSessions().session_scope(org_id) as session:
                tx = GroupTransaction(
                    session,
                    org_id=org_id,
                    members=GroupMembersDB(),
                    entries=GroupEntriesDB(),
                    extensions=GroupExtensionsDB(),
                )
                await tx.add(row)

        assert not isinstance(raised.value, GroupNameTaken)
        assert await _rows(org_id, OrgGroup) == []


class TestACopyIsOneMoment:
    async def test_the_group_its_entries_and_its_extensions_share_one_timestamp(self, org_id):
        source = await GroupsDB().create_group(org_id, settings=GroupSettings(name='Legal'), by_user_id='admin-1')
        await GroupEntriesDB().create_entry(org_id, group_id=source.id, entry=_AGENT, by_user_id='admin-1')
        await GroupExtensionsDB().enable(org_id, source.id, 'ext-a', by_user_id='admin-1')
        ticks = _ticking()

        copy = await GroupsDB(clock=lambda: next(ticks)).copy_group(
            org_id, from_group_id=source.id, settings=GroupSettings(name='Copy'), by_user_id='copier-1'
        )

        (entry,) = await _rows(org_id, OrgGroupEntry, OrgGroupEntry.group_id == copy.id)
        (extension,) = await _rows(org_id, OrgGroupExtension, OrgGroupExtension.group_id == copy.id)
        assert {copy.created_at, copy.updated_at, entry.created_at, entry.updated_at, extension.enabled_at} == {_START}


class _ForgetsNoEnabler(GroupExtensionsDB):
    async def forget_enabler(self, session: AsyncSession, *, user_id: str) -> None:
        raise RuntimeError('the last step of forgetting them failed')


class TestForgettingAPersonInTheGroups:
    async def test_their_membership_goes_and_their_name_is_cleared_from_what_stays(self, org_id):
        legal = await GroupsDB().create_group(org_id, settings=GroupSettings(name='Legal'), by_user_id='gone-1')
        await GroupEntriesDB().create_entry(org_id, group_id=legal.id, entry=_AGENT, by_user_id='gone-1')
        await GroupExtensionsDB().enable(org_id, legal.id, 'ext-a', by_user_id='gone-1')
        await GroupMembersDB().place(org_id, group_id=legal.id, user_id='gone-1', added_by_user_id='admin-1')
        await GroupMembersDB().place(org_id, group_id=legal.id, user_id='dev-1', added_by_user_id='gone-1')

        await GroupPersonErasureDB().forget_person(org_id, user_id='gone-1')

        assert {(m.user_id, m.added_by_user_id) for m in await _rows(org_id, OrgGroupMember)} == {('dev-1', None)}
        assert [g.created_by_user_id for g in await _rows(org_id, OrgGroup)] == [None]
        assert [e.created_by_user_id for e in await _rows(org_id, OrgGroupEntry)] == [None]
        assert [x.enabled_by_user_id for x in await _rows(org_id, OrgGroupExtension)] == [None]

    async def test_a_failure_part_way_clears_nothing(self, org_id):
        legal = await GroupsDB().create_group(org_id, settings=GroupSettings(name='Legal'), by_user_id='gone-1')
        await GroupMembersDB().place(org_id, group_id=legal.id, user_id='gone-1', added_by_user_id='admin-1')

        with pytest.raises(RuntimeError):
            await GroupPersonErasureDB(extensions=_ForgetsNoEnabler()).forget_person(org_id, user_id='gone-1')

        assert [m.user_id for m in await _rows(org_id, OrgGroupMember)] == ['gone-1']
        assert [g.created_by_user_id for g in await _rows(org_id, OrgGroup)] == ['gone-1']


class TestWhatIsASingleton:
    def test_a_row_class_is_one_instance(self):
        assert GroupMembersDB() is GroupMembersDB()
        assert GroupEntriesDB() is GroupEntriesDB()

    def test_an_aggregate_is_its_own_each_time(self):
        """What it was given is what it uses: `test_a_failure_part_way_clears_nothing` relies on that."""
        assert isinstance(GroupPersonErasureDB(), TenantScoped)
        assert GroupPersonErasureDB() is not GroupPersonErasureDB()
