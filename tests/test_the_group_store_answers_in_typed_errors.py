"""The group store says what went wrong by name, and decides no status code.

It used to raise `HTTPException`s from the persistence layer, which meant
a caller that was not an HTTP request had to catch a web framework's
exception, and that every `IntegrityError` from `create_entry` was
"already exists" — including the foreign-key violation from a group
deleted meanwhile. The portal's responses are unchanged: the HTTP tests
pin those, and `GroupRefusals` is what keeps them so.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.base_v1.schema.portal.group_entries import UpdateEntryRequest
from app.api.base_v1.schema.portal.group_theme import GroupTheme
from app.api.base_v1.schema.portal.groups import UpdateGroupRequest
from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.group_extensions import GroupExtensionsDB
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.groups import GroupsDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_group_entry import OrgGroupEntry
from app.db.sql.schemas.group import (
    DefaultGroupKeepsItsPeople,
    EntryCreate,
    EntryNameTaken,
    EntryNotFound,
    EntryPatch,
    GroupNameTaken,
    GroupNotFound,
    GroupPatch,
    GroupSettings,
    MemberNotFound,
    NoDefaultGroup,
    TemplateGroupNotFound,
)
from app.db.sql.tenancy import TenantSessions
from app.services.portal.group_refusals import GroupRefusals
from app.utils.singleton import SingletonMeta
from tests.conftest import create_org, create_user

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


async def _group(org_id: str, name: str, *, is_default: bool = False) -> OrgGroup:
    return await GroupsDB().create_group(
        org_id, settings=GroupSettings(name=name), by_user_id='admin-1', is_default=is_default
    )


async def _count(org_id: str, model: type[OrgGroup] | type[OrgGroupEntry]) -> int:
    async with TenantSessions().session_scope(org_id) as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


class TestNames:
    async def test_a_duplicate_group_name_is_group_name_taken(self, org_id):
        await _group(org_id, 'Legal')

        with pytest.raises(GroupNameTaken) as raised:
            await _group(org_id, 'Legal')

        assert str(raised.value) == "A group named 'Legal' already exists in this organization."
        assert raised.value.name == 'Legal'

    async def test_concurrent_creates_leave_one_group_and_name_the_rest(self, org_id):
        results = await asyncio.gather(*(_group(org_id, 'Legal') for _ in range(6)), return_exceptions=True)

        created = [r for r in results if isinstance(r, OrgGroup)]
        refused = [r for r in results if not isinstance(r, OrgGroup)]
        assert len(created) == 1
        assert refused and all(isinstance(r, GroupNameTaken) for r in refused)

    async def test_a_duplicate_entry_is_entry_name_taken(self, org_id):
        group = await _group(org_id, 'Legal')
        await GroupEntriesDB().create_entry(org_id, group_id=group.id, entry=_AGENT, by_user_id='admin-1')

        with pytest.raises(EntryNameTaken) as raised:
            await GroupEntriesDB().create_entry(org_id, group_id=group.id, entry=_AGENT, by_user_id='admin-1')

        assert str(raised.value) == "An entry named 'reviewer' of kind 'agents' already exists in this group."

    async def test_an_entry_for_a_missing_group_is_not_already_there(self, org_id):
        """The foreign-key violation is itself, not a misleading 409."""
        await _group(org_id, 'Legal')

        with pytest.raises(IntegrityError) as raised:
            await GroupEntriesDB().create_entry(org_id, group_id='no-such-group', entry=_AGENT, by_user_id='admin-1')

        assert not isinstance(raised.value, EntryNameTaken)


class TestWhatIsNotThere:
    async def test_a_missing_entry(self, org_id):
        group = await _group(org_id, 'Legal')

        with pytest.raises(EntryNotFound):
            await GroupEntriesDB().update_entry(org_id, group_id=group.id, entry_id='nope', patch=EntryPatch())
        with pytest.raises(EntryNotFound):
            await GroupEntriesDB().delete_entry(org_id, group_id=group.id, entry_id='nope')

    async def test_a_missing_group(self, org_id):
        await _group(org_id, 'Default', is_default=True)

        with pytest.raises(GroupNotFound):
            await GroupsDB().update_group(org_id, 'nope', patch=GroupPatch())
        with pytest.raises(GroupNotFound):
            await GroupsDB().delete_group(org_id, 'nope')

    async def test_a_missing_member(self, org_id):
        await _group(org_id, 'Default', is_default=True)
        legal = await _group(org_id, 'Legal')

        with pytest.raises(MemberNotFound):
            await GroupMembersDB().return_to_default(org_id, group_id=legal.id, user_id='nobody', by_user_id='admin-1')

    async def test_a_missing_template(self, org_id):
        with pytest.raises(TemplateGroupNotFound) as raised:
            await GroupsDB().copy_group(
                org_id, from_group_id='nope', settings=GroupSettings(name='Copy'), by_user_id='admin-1'
            )

        assert str(raised.value) == 'The group to copy from does not exist in this organization.'


class TestTheDefaultGroup:
    async def test_nobody_is_returned_from_it(self, org_id):
        default = await _group(org_id, 'Default', is_default=True)
        await GroupMembersDB().place(org_id, group_id=default.id, user_id='admin-1', added_by_user_id='admin-1')

        with pytest.raises(DefaultGroupKeepsItsPeople):
            await GroupMembersDB().return_to_default(
                org_id, group_id=default.id, user_id='admin-1', by_user_id='admin-1'
            )

    async def test_deleting_a_group_with_no_default_is_a_bug_not_a_conflict(self, org_id):
        legal = await _group(org_id, 'Legal')

        with pytest.raises(NoDefaultGroup):
            await GroupsDB().delete_group(org_id, legal.id)
        assert await GroupsDB().get_group(org_id, legal.id) is not None


class _FailsAfterEntries(GroupExtensionsDB):
    async def copy_into(
        self, session: AsyncSession, *, from_group_id: str, to_group_id: str, by_user_id: str | None, at: datetime
    ) -> None:
        raise GroupNameTaken('Copy')


class TestACopyIsAllOrNothing:
    async def test_a_failure_after_the_entries_leaves_nothing_behind(self, org_id, monkeypatch):
        source = await _group(org_id, 'Legal')
        await GroupEntriesDB().create_entry(org_id, group_id=source.id, entry=_AGENT, by_user_id='admin-1')
        # A GroupsDB of its own: the singleton cache would hand back the shared one.
        others = {cls: instance for cls, instance in SingletonMeta._instances.items() if cls is not GroupsDB}
        monkeypatch.setattr(SingletonMeta, '_instances', others)
        groups = GroupsDB(extensions=_FailsAfterEntries())

        with pytest.raises(GroupNameTaken):
            await groups.copy_group(
                org_id, from_group_id=source.id, settings=GroupSettings(name='Copy'), by_user_id='admin-1'
            )

        assert await _count(org_id, OrgGroup) == 1
        assert await _count(org_id, OrgGroupEntry) == 1


class TestPartialEdits:
    """Absent leaves a field alone; an empty string clears the ones that can be cleared."""

    def test_an_absent_field_is_not_in_the_group_patch(self):
        assert UpdateGroupRequest(name='Legal').to_patch().model_fields_set == {'name'}

    def test_an_empty_default_model_clears_it(self):
        patch = UpdateGroupRequest(default_model='').to_patch()

        assert patch.model_fields_set == {'default_model'}
        assert patch.default_model is None

    def test_a_theme_that_overrides_nothing_clears_it(self):
        patch = UpdateGroupRequest(theme=GroupTheme()).to_patch()

        assert patch.model_fields_set == {'theme'}
        assert patch.theme is None

    def test_a_name_or_description_cannot_be_cleared(self):
        with pytest.raises(ValidationError):
            GroupPatch(name=None)
        with pytest.raises(ValidationError):
            GroupPatch(description=None)

    def test_an_entry_patch_carries_only_what_was_sent(self):
        assert UpdateEntryRequest(enabled=False).to_patch().model_fields_set == {'enabled'}
        cleared = UpdateEntryRequest(model='').to_patch()
        assert cleared.model_fields_set == {'model'}
        assert cleared.model is None

    async def test_the_store_leaves_what_the_patch_does_not_name(self, org_id):
        group = await GroupsDB().create_group(
            org_id,
            settings=GroupSettings(name='Legal', description='Contracts', default_model='m1'),
            by_user_id='admin-1',
        )

        cleared = await GroupsDB().update_group(org_id, group.id, patch=GroupPatch(default_model=None))

        assert (cleared.name, cleared.description, cleared.default_model) == ('Legal', 'Contracts', None)


class TestGroupRefusals:
    def test_a_named_refusal_becomes_the_portals_answer(self):
        with pytest.raises(HTTPException) as raised, GroupRefusals():
            raise MemberNotFound()

        assert (raised.value.status_code, raised.value.detail) == (404, 'Member not found')

    def test_the_default_group_refusal_says_how_to_move_somebody(self):
        with pytest.raises(HTTPException) as raised, GroupRefusals():
            raise DefaultGroupKeepsItsPeople()

        assert raised.value.status_code == 409
        assert 'POST /portal/groups/{group_id}/members' in str(raised.value.detail)

    def test_a_missing_default_group_is_left_to_be_a_500(self):
        with pytest.raises(NoDefaultGroup), GroupRefusals():
            raise NoDefaultGroup('org-1')
