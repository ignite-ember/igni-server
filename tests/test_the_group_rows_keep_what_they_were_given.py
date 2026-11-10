"""What each of the group store's row classes writes, moves and forgets.

`GroupsDB` was one class that owned groups, members, entries and
extensions. It became an aggregate over `GroupMembersDB`,
`GroupEntriesDB`, `GroupExtensionsDB` and `GroupSummariesDB`, and the
HTTP tests only see what comes out the other end. These pin each piece
on its own: a copy keeps every column and is stamped by whoever copied
it, placing somebody where they already are changes nothing, taking
somebody out of a group stamps who did it, and forgetting a person
clears their name without taking away what they made.

The refusals themselves are in
`test_the_group_store_answers_in_typed_errors.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.group_extensions import GroupExtensionsDB
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.group_summaries import GroupSummariesDB
from app.db.sql.groups import GroupsDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_group_entry import OrgGroupEntry
from app.db.sql.models.org_group_extension import OrgGroupExtension
from app.db.sql.models.org_group_member import OrgGroupMember
from app.db.sql.schemas.group import (
    DefaultGroupKeepsItsPeople,
    EntryCreate,
    EntryPatch,
    GroupNameTaken,
    GroupSettings,
    GroupSummary,
)
from app.db.sql.tenancy import TenantSessions
from app.utils.singleton import SingletonMeta
from tests.conftest import create_org, create_user, put_in_org

_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

_PLUGIN = EntryCreate(
    kind='plugins',
    entry_name='review-kit',
    content='{}',
    content_type='json',
    enabled=False,
    source_url='https://example.com/review-kit.git',
    source_ref='v1',
    source_subdir='kit',
)
_AGENT = EntryCreate(
    kind='agents',
    entry_name='reviewer',
    content='---\nname: reviewer\n---\nBody.',
    content_type='markdown',
    enabled=True,
    model='legal-reviewer',
)


@pytest.fixture
async def org_id(db) -> str:
    await create_user(db, user_id='admin-1', email='admin@test.com')
    return str((await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')).id)


async def _group(org_id: str, name: str, *, is_default: bool = False) -> OrgGroup:
    return await GroupsDB().create_group(
        org_id, settings=GroupSettings(name=name), by_user_id='admin-1', is_default=is_default
    )


async def _rows(org_id: str, model, *where):
    async with TenantSessions().session_scope(org_id) as session:
        return list((await session.execute(select(model).where(*where))).scalars().all())


async def _membership(org_id: str, user_id: str) -> OrgGroupMember:
    (row,) = await _rows(org_id, OrgGroupMember, OrgGroupMember.user_id == user_id)
    return row


async def _in_a_transaction(org_id: str, work) -> None:
    async with TenantSessions().session_scope(org_id) as session:
        await work(session)


# ----------------------------------------------------------------------
# Entries
# ----------------------------------------------------------------------


class TestEntries:
    async def test_they_are_listed_by_kind_then_name(self, org_id):
        group = await _group(org_id, 'Legal')
        for kind, name in (('skills', 'b'), ('agents', 'z'), ('agents', 'a')):
            await GroupEntriesDB().create_entry(
                org_id,
                group_id=group.id,
                entry=EntryCreate(kind=kind, entry_name=name, content='x', content_type='markdown', enabled=True),
                by_user_id='admin-1',
            )

        listed = await GroupEntriesDB().list_entries(org_id, group.id)

        assert [(e.kind, e.entry_name) for e in listed] == [('agents', 'a'), ('agents', 'z'), ('skills', 'b')]

    async def test_one_is_found_only_in_its_own_group(self, org_id):
        legal = await _group(org_id, 'Legal')
        eng = await _group(org_id, 'Eng')
        entry = await GroupEntriesDB().create_entry(org_id, group_id=legal.id, entry=_AGENT, by_user_id='admin-1')

        assert (await GroupEntriesDB().get_entry(org_id, group_id=legal.id, entry_id=entry.id)) is not None
        assert await GroupEntriesDB().get_entry(org_id, group_id=eng.id, entry_id=entry.id) is None

    async def test_a_new_one_keeps_every_field_and_who_made_it(self, org_id):
        group = await _group(org_id, 'Legal')

        made = await GroupEntriesDB().create_entry(org_id, group_id=group.id, entry=_PLUGIN, by_user_id='admin-1')

        (row,) = await _rows(org_id, OrgGroupEntry, OrgGroupEntry.id == made.id)
        assert (
            row.kind,
            row.entry_name,
            row.content,
            row.content_type,
            row.enabled,
            row.source_url,
            row.source_ref,
            row.source_subdir,
            row.created_by_user_id,
        ) == (
            'plugins',
            'review-kit',
            '{}',
            'json',
            False,
            'https://example.com/review-kit.git',
            'v1',
            'kit',
            'admin-1',
        )

    async def test_an_explicit_none_model_clears_it(self, org_id):
        group = await _group(org_id, 'Legal')
        entry = await GroupEntriesDB().create_entry(org_id, group_id=group.id, entry=_AGENT, by_user_id='admin-1')

        edited = await GroupEntriesDB().update_entry(
            org_id, group_id=group.id, entry_id=entry.id, patch=EntryPatch(model=None)
        )

        assert edited.model is None
        assert (edited.content, edited.enabled) == (_AGENT.content, True)
        (row,) = await _rows(org_id, OrgGroupEntry, OrgGroupEntry.id == entry.id)
        assert row.model is None

    async def test_content_and_enabled_change_and_the_model_stays(self, org_id):
        group = await _group(org_id, 'Legal')
        entry = await GroupEntriesDB().create_entry(org_id, group_id=group.id, entry=_AGENT, by_user_id='admin-1')

        await GroupEntriesDB().update_entry(
            org_id, group_id=group.id, entry_id=entry.id, patch=EntryPatch(content='New body.', enabled=False)
        )

        (row,) = await _rows(org_id, OrgGroupEntry, OrgGroupEntry.id == entry.id)
        assert (row.content, row.enabled, row.model) == ('New body.', False, 'legal-reviewer')
        assert row.updated_at > row.created_at

    async def test_a_deleted_one_is_gone(self, org_id):
        group = await _group(org_id, 'Legal')
        entry = await GroupEntriesDB().create_entry(org_id, group_id=group.id, entry=_AGENT, by_user_id='admin-1')

        await GroupEntriesDB().delete_entry(org_id, group_id=group.id, entry_id=entry.id)

        assert await _rows(org_id, OrgGroupEntry) == []

    async def test_forgetting_a_creator_clears_only_their_name(self, org_id):
        group = await _group(org_id, 'Legal')
        theirs = await GroupEntriesDB().create_entry(org_id, group_id=group.id, entry=_AGENT, by_user_id='gone-1')
        others = await GroupEntriesDB().create_entry(org_id, group_id=group.id, entry=_PLUGIN, by_user_id='admin-1')

        await _in_a_transaction(org_id, lambda s: GroupEntriesDB().forget_creator(s, user_id='gone-1'))

        kept = {row.id: row.created_by_user_id for row in await _rows(org_id, OrgGroupEntry)}
        assert kept == {theirs.id: None, others.id: 'admin-1'}


class TestEntryPatch:
    def test_content_and_enabled_can_be_changed_but_not_cleared(self):
        with pytest.raises(ValidationError):
            EntryPatch(content=None)
        with pytest.raises(ValidationError):
            EntryPatch(enabled=None)

    def test_an_explicit_none_model_is_supplied(self):
        assert EntryPatch(model=None).model_fields_set == {'model'}
        assert EntryPatch().model_fields_set == set()


# ----------------------------------------------------------------------
# A copy
# ----------------------------------------------------------------------


class TestACopy:
    async def test_every_entry_comes_across_whole_and_is_the_copiers(self, org_id):
        source = await _group(org_id, 'Legal')
        await GroupEntriesDB().create_entry(org_id, group_id=source.id, entry=_PLUGIN, by_user_id='admin-1')
        await GroupEntriesDB().create_entry(org_id, group_id=source.id, entry=_AGENT, by_user_id='admin-1')

        copy = await GroupsDB(clock=lambda: _AT).copy_group(
            org_id, from_group_id=source.id, settings=GroupSettings(name='Copy'), by_user_id='copier-1'
        )

        def _fields(row: OrgGroupEntry) -> tuple:
            return (
                row.kind,
                row.entry_name,
                row.content,
                row.content_type,
                row.enabled,
                row.source_url,
                row.source_ref,
                row.source_subdir,
                row.model,
            )

        originals = await _rows(org_id, OrgGroupEntry, OrgGroupEntry.group_id == source.id)
        copied = await _rows(org_id, OrgGroupEntry, OrgGroupEntry.group_id == copy.id)
        assert sorted(map(_fields, copied)) == sorted(map(_fields, originals))
        assert {row.id for row in copied}.isdisjoint({row.id for row in originals})
        assert {(row.created_by_user_id, row.created_at, row.updated_at) for row in copied} == {('copier-1', _AT, _AT)}

    async def test_the_extensions_come_across_switched_on_by_the_copier(self, org_id):
        source = await _group(org_id, 'Legal')
        await GroupExtensionsDB().enable(org_id, source.id, 'ext-a', by_user_id='admin-1')
        await GroupExtensionsDB().enable(org_id, source.id, 'ext-b', by_user_id='admin-1')

        copy = await GroupsDB(clock=lambda: _AT).copy_group(
            org_id, from_group_id=source.id, settings=GroupSettings(name='Copy'), by_user_id='copier-1'
        )

        copied = await _rows(org_id, OrgGroupExtension, OrgGroupExtension.group_id == copy.id)
        assert sorted((row.extension_id, row.enabled_by_user_id, row.enabled_at) for row in copied) == [
            ('ext-a', 'copier-1', _AT),
            ('ext-b', 'copier-1', _AT),
        ]
        assert await GroupExtensionsDB().enabled_in(org_id, source.id) == {'ext-a', 'ext-b'}

    async def test_the_copy_is_made_with_the_callers_settings(self, org_id):
        """The source's VM profile comes across too; `test_vm_profiles.py` pins that."""
        source = await GroupsDB().create_group(
            org_id, settings=GroupSettings(name='Legal', description='Theirs', default_model='m1'), by_user_id='admin-1'
        )

        copy = await GroupsDB(clock=lambda: _AT).copy_group(
            org_id,
            from_group_id=source.id,
            settings=GroupSettings(name='Copy', description='Mine', default_model='m2'),
            by_user_id='copier-1',
        )

        assert (copy.name, copy.description, copy.default_model, copy.is_default) == ('Copy', 'Mine', 'm2', False)
        assert (copy.created_by_user_id, copy.created_at, copy.updated_at) == ('copier-1', _AT, _AT)


# ----------------------------------------------------------------------
# Extensions
# ----------------------------------------------------------------------


class TestExtensions:
    async def test_switching_on_twice_keeps_who_switched_it_on_first(self, org_id):
        group = await _group(org_id, 'Legal')

        await GroupExtensionsDB().enable(org_id, group.id, 'ext-a', by_user_id='admin-1')
        await GroupExtensionsDB().enable(org_id, group.id, 'ext-a', by_user_id='admin-2')

        (row,) = await _rows(org_id, OrgGroupExtension)
        assert row.enabled_by_user_id == 'admin-1'

    async def test_switching_off_what_is_off_changes_nothing(self, org_id):
        group = await _group(org_id, 'Legal')
        await GroupExtensionsDB().enable(org_id, group.id, 'ext-a', by_user_id='admin-1')

        await GroupExtensionsDB().disable(org_id, group.id, 'ext-a')
        await GroupExtensionsDB().disable(org_id, group.id, 'ext-a')

        assert await GroupExtensionsDB().enabled_in(org_id, group.id) == set()

    async def test_each_extension_names_the_groups_it_is_on_for(self, org_id):
        legal = await _group(org_id, 'Legal')
        eng = await _group(org_id, 'Eng')
        await GroupExtensionsDB().enable(org_id, legal.id, 'ext-a', by_user_id='admin-1')
        await GroupExtensionsDB().enable(org_id, eng.id, 'ext-a', by_user_id='admin-1')
        await GroupExtensionsDB().enable(org_id, eng.id, 'ext-b', by_user_id='admin-1')

        enabling = await GroupExtensionsDB().groups_enabling(org_id)

        assert enabling == {'ext-a': sorted([legal.id, eng.id]), 'ext-b': [eng.id]}

    async def test_forgetting_an_enabler_leaves_it_switched_on(self, org_id):
        group = await _group(org_id, 'Legal')
        await GroupExtensionsDB().enable(org_id, group.id, 'ext-a', by_user_id='gone-1')
        await GroupExtensionsDB().enable(org_id, group.id, 'ext-b', by_user_id='admin-1')

        await _in_a_transaction(org_id, lambda s: GroupExtensionsDB().forget_enabler(s, user_id='gone-1'))

        assert await GroupExtensionsDB().enabled_in(org_id, group.id) == {'ext-a', 'ext-b'}
        enablers = {row.extension_id: row.enabled_by_user_id for row in await _rows(org_id, OrgGroupExtension)}
        assert enablers == {'ext-a': None, 'ext-b': 'admin-1'}


# ----------------------------------------------------------------------
# Members
# ----------------------------------------------------------------------


class TestPlacingSomebody:
    async def test_placing_them_where_they_already_are_changes_nothing(self, org_id):
        legal = await _group(org_id, 'Legal')
        first = await GroupMembersDB().place(org_id, group_id=legal.id, user_id='dev-1', added_by_user_id='admin-1')

        again = await GroupMembersDB().place(org_id, group_id=legal.id, user_id='dev-1', added_by_user_id='admin-2')

        assert (again.id, again.added_by_user_id, again.added_at) == (first.id, 'admin-1', first.added_at)

    async def test_placing_them_elsewhere_moves_them_and_says_who_did(self, org_id):
        legal = await _group(org_id, 'Legal')
        eng = await _group(org_id, 'Eng')
        first = await GroupMembersDB().place(org_id, group_id=legal.id, user_id='dev-1', added_by_user_id='admin-1')

        moved = await GroupMembersDB().place(org_id, group_id=eng.id, user_id='dev-1', added_by_user_id='admin-2')

        assert (moved.group_id, moved.added_by_user_id) == (eng.id, 'admin-2')
        assert moved.added_at > first.added_at
        assert len(await _rows(org_id, OrgGroupMember)) == 1

    async def test_only_the_ungrouped_are_placed_and_counted(self, org_id):
        default = await _group(org_id, 'Default', is_default=True)
        legal = await _group(org_id, 'Legal')
        await GroupMembersDB().place(org_id, group_id=legal.id, user_id='dev-1', added_by_user_id='admin-1')

        placed = await GroupMembersDB().place_if_ungrouped(
            org_id, group_id=default.id, user_ids=['dev-1', 'dev-2', 'dev-3'], added_by_user_id='admin-1'
        )

        assert placed == 2
        assert (await _membership(org_id, 'dev-1')).group_id == legal.id
        assert {(await _membership(org_id, u)).group_id for u in ('dev-2', 'dev-3')} == {default.id}
        assert (
            await GroupMembersDB().place_if_ungrouped(
                org_id, group_id=default.id, user_ids=[], added_by_user_id='admin-1'
            )
            == 0
        )

    async def test_a_groups_people_are_listed_in_the_order_they_joined(self, org_id):
        legal = await _group(org_id, 'Legal')
        for user_id in ('dev-3', 'dev-1', 'dev-2'):
            await GroupMembersDB().place(org_id, group_id=legal.id, user_id=user_id, added_by_user_id='admin-1')

        assert [m.user_id for m in await GroupMembersDB().list_members(org_id, legal.id)] == ['dev-3', 'dev-1', 'dev-2']

    async def test_every_membership_is_listed_by_person_with_its_group(self, org_id):
        default = await _group(org_id, 'Default', is_default=True)
        legal = await _group(org_id, 'Legal')
        await GroupMembersDB().place(org_id, group_id=legal.id, user_id='dev-b', added_by_user_id='admin-1')
        await GroupMembersDB().place(org_id, group_id=default.id, user_id='dev-a', added_by_user_id='admin-1')

        memberships = await GroupMembersDB().memberships(org_id)

        assert [(m.user_id, m.group_id, m.group_name, m.is_default) for m in memberships] == [
            ('dev-a', default.id, 'Default', True),
            ('dev-b', legal.id, 'Legal', False),
        ]


class TestTakingSomebodyOut:
    async def test_they_go_back_to_default_stamped_by_whoever_did_it(self, org_id):
        default = await _group(org_id, 'Default', is_default=True)
        legal = await _group(org_id, 'Legal')
        placed = await GroupMembersDB().place(org_id, group_id=legal.id, user_id='dev-1', added_by_user_id='admin-1')

        await GroupMembersDB().return_to_default(org_id, group_id=legal.id, user_id='dev-1', by_user_id='admin-2')

        row = await _membership(org_id, 'dev-1')
        assert (row.group_id, row.added_by_user_id) == (default.id, 'admin-2')
        assert row.added_at > placed.added_at

    async def test_the_store_says_why_nobody_leaves_default(self):
        """The store's own words; the portal answers with its own copy that names the route."""
        assert str(DefaultGroupKeepsItsPeople()) == (
            'Nobody leaves the Default group except by being placed in another group.'
        )


class _MovesThenFails(GroupMembersDB):
    async def return_everyone_in(self, session: AsyncSession, *, org_id: str, from_group_id: str) -> None:
        await super().return_everyone_in(session, org_id=org_id, from_group_id=from_group_id)
        raise RuntimeError('the delete went wrong after the move')


class TestDeletingAGroup:
    async def test_its_people_go_to_default_and_nobody_else_moves(self, org_id):
        default = await _group(org_id, 'Default', is_default=True)
        legal = await _group(org_id, 'Legal')
        eng = await _group(org_id, 'Eng')
        for user_id in ('dev-1', 'dev-2'):
            await GroupMembersDB().place(org_id, group_id=legal.id, user_id=user_id, added_by_user_id='admin-1')
        await GroupMembersDB().place(org_id, group_id=eng.id, user_id='dev-3', added_by_user_id='admin-1')

        await GroupsDB().delete_group(org_id, group_id=legal.id)

        assert {m.user_id: m.group_id for m in await GroupMembersDB().memberships(org_id)} == {
            'dev-1': default.id,
            'dev-2': default.id,
            'dev-3': eng.id,
        }
        assert await GroupsDB().get_group(org_id, group_id=legal.id) is None

    async def test_the_move_and_the_delete_are_one_transaction(self, org_id):
        await _group(org_id, 'Default', is_default=True)
        legal = await _group(org_id, 'Legal')
        await GroupMembersDB().place(org_id, group_id=legal.id, user_id='dev-1', added_by_user_id='admin-1')

        with pytest.raises(RuntimeError):
            await GroupsDB(members=_MovesThenFails()).delete_group(org_id, group_id=legal.id)

        assert (await _membership(org_id, 'dev-1')).group_id == legal.id
        assert await GroupsDB().get_group(org_id, group_id=legal.id) is not None


class TestForgettingAPerson:
    async def test_their_membership_goes_and_their_name_on_others_is_cleared(self, org_id):
        legal = await _group(org_id, 'Legal')
        await GroupMembersDB().place(org_id, group_id=legal.id, user_id='gone-1', added_by_user_id='admin-1')
        await GroupMembersDB().place(org_id, group_id=legal.id, user_id='dev-1', added_by_user_id='gone-1')
        await GroupMembersDB().place(org_id, group_id=legal.id, user_id='dev-2', added_by_user_id='admin-1')

        await _in_a_transaction(org_id, lambda s: GroupMembersDB().forget(s, user_id='gone-1'))

        left = {row.user_id: row.added_by_user_id for row in await _rows(org_id, OrgGroupMember)}
        assert left == {'dev-1': None, 'dev-2': 'admin-1'}


# ----------------------------------------------------------------------
# Whose group, from the person
# ----------------------------------------------------------------------


class TestGroupOf:
    async def test_it_answers_for_the_organisation_asked_about_only(self, org_id):
        """A group row that names another organisation is not this one's, even in this database."""
        stray = OrgGroup(
            id='stray-group',
            org_id='elsewhere',
            name='Stray',
            is_default=False,
            created_at=_AT,
            updated_at=_AT,
        )
        await put_in_org(org_id, stray, OrgGroupMember(id='m-1', group_id='stray-group', user_id='dev-1', added_at=_AT))
        legal = await _group(org_id, 'Legal')
        await GroupMembersDB().place(org_id, group_id=legal.id, user_id='dev-2', added_by_user_id='admin-1')

        assert await GroupMembersDB().group_of(org_id, 'dev-1') is None
        assert await GroupMembersDB().group_of(org_id, 'dev-2') == GroupSummary(
            id=legal.id, org_id=org_id, name='Legal'
        )

    def test_a_summary_is_the_groups_id_org_and_name(self):
        group = OrgGroup(id='g-1', org_id='org-1', name='Legal')

        assert GroupSummary.of(group) == GroupSummary(id='g-1', org_id='org-1', name='Legal')


class _Organisations:
    """Two active memberships, which the shared plane allows one person no longer but the crossing must handle."""

    def __init__(self, active: list[str], every: list[str]) -> None:
        self._active = active
        self._every = every

    async def member_org_ids(self, user_id: str, *, active_only: bool = True) -> list[str]:
        return list(self._active if active_only else self._every)


@pytest.fixture
def summaries_with():
    """A `GroupSummariesDB` over the organisations given, past its singleton."""

    def build(organisations: _Organisations) -> GroupSummariesDB:
        SingletonMeta._instances.pop(GroupSummariesDB, None)
        return GroupSummariesDB(organizations=organisations)  # type: ignore[arg-type]

    yield build
    SingletonMeta._instances.pop(GroupSummariesDB, None)


class TestAPersonsGroup:
    @pytest.fixture
    async def two_orgs(self, db, org_id) -> tuple[str, str, str, str]:
        await create_user(db, user_id='owner-2', email='owner2@test.com')
        other = str((await create_org(db, owner_user_id='owner-2', slug='other', display_name='Other')).id)
        mine = await _group(org_id, 'Legal')
        theirs = await GroupsDB().create_group(other, settings=GroupSettings(name='Eng'), by_user_id='owner-2')
        await GroupMembersDB().place(org_id, group_id=mine.id, user_id='dev-1', added_by_user_id='admin-1')
        await GroupMembersDB().place(other, group_id=theirs.id, user_id='dev-1', added_by_user_id='owner-2')
        return org_id, mine.id, other, theirs.id

    async def test_it_is_the_group_of_the_organisation_they_are_signed_into(self, two_orgs, summaries_with):
        first, first_group, second, second_group = two_orgs
        summaries = summaries_with(_Organisations(active=[first, second], every=[first, second]))

        signed_into_second = await summaries.get_active_user_group_summary('dev-1', second)

        assert signed_into_second == GroupSummary(id=second_group, org_id=second, name='Eng')

    async def test_without_an_organisation_the_first_with_a_group_answers(self, two_orgs, summaries_with):
        first, first_group, second, _ = two_orgs
        summaries = summaries_with(_Organisations(active=['no-group-org', first, second], every=[]))

        assert await summaries.get_active_user_group_summary('dev-1') == GroupSummary(
            id=first_group, org_id=first, name='Legal'
        )

    async def test_nothing_where_they_are_not_active(self, two_orgs, summaries_with):
        first, _, second, second_group = two_orgs
        summaries = summaries_with(_Organisations(active=[first], every=[first, second]))

        assert await summaries.get_active_user_group_summary('dev-1', second) is None

    async def test_the_admin_view_reaches_where_they_are_not_active(self, two_orgs, summaries_with):
        first, _, second, second_group = two_orgs
        summaries = summaries_with(_Organisations(active=[], every=[second, first]))

        assert await summaries.get_active_user_group_summary('dev-1') is None
        assert await summaries.get_user_group_summary('dev-1') == GroupSummary(
            id=second_group, org_id=second, name='Eng'
        )


# ----------------------------------------------------------------------
# The groups themselves
# ----------------------------------------------------------------------


class TestListingGroups:
    async def test_oldest_first_paged_with_the_total(self, org_id):
        names = ['A', 'B', 'C', 'D']
        for index, name in enumerate(names):
            await GroupsDB(clock=lambda i=index: _AT.replace(minute=i)).create_group(
                org_id, settings=GroupSettings(name=name), by_user_id='admin-1'
            )

        page = await GroupsDB().list_groups(org_id, offset=1, limit=2)

        assert ([g.name for g in page.items], page.total) == (['B', 'C'], 4)

    async def test_a_limit_of_zero_is_every_group(self, org_id):
        for name in ('A', 'B', 'C'):
            await _group(org_id, name)

        page = await GroupsDB().list_groups(org_id, limit=0)

        assert (len(page.items), page.total) == (3, 3)


class TestAnIntegrityFailureThatIsNotTheName:
    async def test_a_missing_name_is_itself_not_name_taken(self, org_id):
        """A NOT NULL on `name` mentions the column, not the constraint; it is not "already exists"."""
        with pytest.raises(IntegrityError) as raised:
            await GroupsDB().create_group(
                org_id, settings=GroupSettings.model_construct(name=None), by_user_id='admin-1'
            )

        assert not isinstance(raised.value, GroupNameTaken)
