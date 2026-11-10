"""An edited entry answers with everything it holds, its plugin source included.

`update_entry` used to build its response by hand and leave out
`source_url`, `source_ref` and `source_subdir`, so an edited plugin came
back looking as though it had no source while `create_entry` and
`list_entries` reported one. Every entry response is now built by
`OrgGroupEntryResponse.of`, and this pins the update path to it.
"""

from __future__ import annotations

import pytest

from app.api.base_v1.schema.portal.group_entries import CreateEntryRequest, UpdateEntryRequest
from app.db.sql.groups import GroupsDB
from app.db.sql.schemas.group import GroupSettings
from app.services.portal.group_entries_service import GroupEntriesService
from tests.conftest import create_org, create_user


@pytest.fixture
async def org_id(db) -> str:
    await create_user(db, user_id='admin-1', email='admin@test.com')
    return str((await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')).id)


async def test_an_edited_plugin_still_names_its_source(org_id):
    group = await GroupsDB().create_group(org_id, settings=GroupSettings(name='Legal'), by_user_id='admin-1')
    service = GroupEntriesService()
    created = await service.create_entry(
        org_id=org_id,
        group_id=group.id,
        body=CreateEntryRequest(
            kind='plugins',
            entry_name='linter',
            content='name: linter\n',
            content_type='yaml',
            source_url='https://example.com/acme/linter.git',
            source_ref='v1.2.0',
            source_subdir='plugins/linter',
        ),
        by_user_id='admin-1',
    )

    updated = await service.update_entry(
        org_id=org_id,
        group_id=group.id,
        entry_id=created.id,
        body=UpdateEntryRequest(content='name: linter\nversion: 2\n', enabled=False),
    )

    assert updated.model_dump(exclude={'updated_at'}) == {
        'id': created.id,
        'group_id': group.id,
        'kind': 'plugins',
        'entry_name': 'linter',
        'content': 'name: linter\nversion: 2\n',
        'content_type': 'yaml',
        'enabled': False,
        'source_url': 'https://example.com/acme/linter.git',
        'source_ref': 'v1.2.0',
        'source_subdir': 'plugins/linter',
        'model': None,
        'created_by_user_id': 'admin-1',
        'created_at': created.created_at,
    }
