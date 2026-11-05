"""A person who joins and is removed before anything reads the feed still gets a removed incarnation.

The feed numbers marks when it is read, and a mark names a producer, not
the incarnation it was. A membership that started and ended between two
reads left nothing to number: the person was never listed, so never
removed — no erasure scheduled, and an export of their data refused
with 404 for good. Removing somebody now catches the feed up first and
numbers the removal after (`ProducerFeed.removal`).
"""

from __future__ import annotations

import pytest

from tests.cloud_world import CloudOrg, platform

pytestmark = pytest.mark.anyio


def _rows_of(page: dict, user_id: str) -> list[dict]:
    return [p for p in page['producers'] if p['producer'] == {'kind': 'person', 'id': user_id}]


async def test_removed_by_an_admin_with_no_read_in_between(client):
    acme = await CloudOrg(client).create()
    ada, _ = await acme.person('ada@acme.com')

    removed = await client.delete(f'/portal/team/{ada}', headers=acme.admin)
    export = await client.post(
        f'/portal/orgs/{acme.org_id}/producers/person/{ada}/exports', json={}, headers=acme.admin
    )
    page = (await client.get('/platform/producers', headers=await platform(client))).json()

    assert removed.status_code in (200, 204), removed.text
    [row] = _rows_of(page, ada)
    assert (row['removed'], row['active'], row['erase_at'] is not None) == (True, False, True)
    assert export.status_code == 202, export.text
    assert export.json()['incarnation'] == row['incarnation']


async def test_an_account_deleted_with_no_read_in_between(client):
    acme = await CloudOrg(client).create()
    ada, ada_headers = await acme.person('ada@acme.com')

    deleted = await client.delete('/portal/me', headers=ada_headers)
    page = (await client.get('/platform/producers', headers=await platform(client))).json()

    assert deleted.status_code in (200, 204), deleted.text
    [row] = _rows_of(page, ada)
    assert (row['removed'], row['active'], row['erase_at'] is not None) == (True, False, True)
