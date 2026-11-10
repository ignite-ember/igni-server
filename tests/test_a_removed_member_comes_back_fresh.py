"""A removed member's sessions for that organisation end; added again, they are a new member.

An admin adds an existing account back with the invite call — a new
membership, so a new incarnation with a fresh VM (`docs/service-identity.md`
§11.3). A session pinned to the organisation and signed in under the
old membership is refused for good, everywhere igni server checks one.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from tests.cloud_world import platform, shared_plane
from tests.conftest import add_member, auth_header, create_org, create_user, make_token

pytestmark = pytest.mark.anyio


@pytest.fixture
async def acme(client):
    async with shared_plane() as db:
        owner = await create_user(db, email='owner@acme.com')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug='acme', domains=('acme.com',))
        ada = await create_user(db, email='ada@acme.com')
        await add_member(db, str(org.id), str(ada.user_id))
    org_id = str(org.id)
    return {
        'org_id': org_id,
        'admin': auth_header(make_token(str(owner.user_id), org_id=org_id)),
        'ada': str(ada.user_id),
    }


async def _remove_and_add_back(client, acme) -> dict:
    removed = await client.delete(f'/portal/team/{acme["ada"]}', headers=acme['admin'])
    assert removed.status_code in (200, 204), removed.text
    added = await client.post(
        '/portal/team/invite', json={'email': 'ada@acme.com', 'role': 'member'}, headers=acme['admin']
    )
    assert added.status_code == 201, added.text
    return dict(added.json())


async def test_an_existing_account_is_added_back_as_a_new_incarnation(client, acme):
    gateway = await platform(client)
    before = (await client.get('/platform/producers', headers=gateway)).json()
    first = next(r for r in before['producers'] if r['producer']['id'] == acme['ada'])

    added = await _remove_and_add_back(client, acme)
    rows = (await client.get('/platform/producers', params={'since': before['cursor']}, headers=gateway)).json()

    assert (added['user_id'], added['status'], added['role']) == (acme['ada'], 'active', 'member')
    mine = [
        (r['incarnation'], r['active'], r['removed']) for r in rows['producers'] if r['producer']['id'] == acme['ada']
    ]
    assert mine[0] == (first['incarnation'], False, True)
    assert mine[-1][1:] == (True, False)
    assert mine[-1][0] != first['incarnation']


async def test_adding_a_member_already_there_is_refused(client, acme):
    response = await client.post(
        '/portal/team/invite', json={'email': 'ada@acme.com', 'role': 'member'}, headers=acme['admin']
    )

    assert response.status_code == 409
    assert 'already in this organization' in response.json()['detail']


async def _invite_bob(client, acme):
    return await client.post(
        '/portal/team/invite', json={'email': 'bob@acme.com', 'role': 'member'}, headers=acme['admin']
    )


async def _bob_in_globex(status: str) -> str:
    async with shared_plane() as db:
        globex_owner = await create_user(db, email='owner@globex.com')
        globex = await create_org(db, owner_user_id=str(globex_owner.user_id), slug='globex')
        bob = await create_user(db, email='bob@acme.com')
        await add_member(db, str(globex.id), str(bob.user_id), status=status)
    return str(bob.user_id)


async def _memberships_of(user_id: str) -> list[str]:
    async with shared_plane() as db:
        rows = await db.execute(
            text('SELECT status FROM org_memberships WHERE user_id = :u ORDER BY status'), {'u': user_id}
        )
        return list(rows.scalars().all())


async def test_an_account_that_is_a_member_of_another_organisation_is_refused(client, acme):
    bob = await _bob_in_globex('active')

    response = await _invite_bob(client, acme)

    assert response.status_code == 409
    assert response.json()['detail'] == 'bob@acme.com is a member of another organization'
    assert await _memberships_of(bob) == ['active']


async def test_a_membership_elsewhere_that_is_not_active_does_not_keep_an_account_out(client, acme):
    bob = await _bob_in_globex('invited')

    response = await _invite_bob(client, acme)

    assert response.status_code == 201, response.text
    assert (response.json()['user_id'], response.json()['status']) == (bob, 'active')
    assert await _memberships_of(bob) == ['active', 'invited']


async def test_a_deactivated_account_is_refused_until_it_is_reactivated(client, acme):
    async with shared_plane() as db:
        bob = await create_user(db, email='bob@acme.com')
        await db.execute(text('UPDATE users SET deactivated_at = now() WHERE user_id = :u'), {'u': str(bob.user_id)})
        await db.commit()

    response = await _invite_bob(client, acme)

    assert response.status_code == 409
    assert response.json()['detail'] == 'The account of bob@acme.com is deactivated; reactivate it first'
    assert await _memberships_of(str(bob.user_id)) == []


async def test_a_session_for_the_organisation_ends_with_the_membership(client, acme):
    old = auth_header(make_token(acme['ada'], org_id=acme['org_id']))
    assert (await client.get('/portal/vm-credentials', headers=old)).status_code == 200

    await client.delete(f'/portal/team/{acme["ada"]}', headers=acme['admin'])
    gone = await client.get('/portal/vm-credentials', headers=old)
    await asyncio.sleep(1.1)
    await client.post('/portal/team/invite', json={'email': 'ada@acme.com', 'role': 'member'}, headers=acme['admin'])
    back = await client.get('/portal/vm-credentials', headers=old)
    chat = await client.post('/chat/completions', headers=old, json={'messages': [{'role': 'user', 'content': 'hi'}]})
    signed_in_again = auth_header(make_token(acme['ada'], org_id=acme['org_id']))
    fresh = await client.get('/portal/vm-credentials', headers=signed_in_again)

    assert gone.status_code == 403
    assert back.status_code == 401
    assert 'earlier membership' in back.json()['detail']
    assert chat.status_code == 401
    assert fresh.status_code == 200
