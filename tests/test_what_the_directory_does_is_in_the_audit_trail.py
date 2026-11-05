"""What an identity provider does over SCIM is in the audit trail.

Creating somebody, deactivating them, bringing them back and deleting
them changed accounts and memberships and left no row: `/scim/v2` was
not one of the audited surfaces. Each is recorded now, in the person's
organisation's own trail — where its admins read — attributed to the
identity provider rather than to a person. A provider refused for a
wrong token, or a person on no organisation's domain, is in the
deployment's trail.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.config import settings
from app.security.scim_token import SCIM_ACTOR
from tests.conftest import audit_rows, create_org, create_user, TEST_DB_URL_SYNC

SCIM = 'http://test/scim/v2'
TOKEN = 'scim-token-for-tests'
USER_SCHEMA = 'urn:ietf:params:scim:schemas:core:2.0:User'
PATCH_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:PatchOp'


@pytest.fixture(autouse=True)
def _scim_configured(monkeypatch):
    monkeypatch.setattr(settings, 'scim_token', TOKEN)


@pytest.fixture
async def org_id(db) -> str:
    await create_user(db, user_id='admin', email='admin@acme.test')
    org = await create_org(db, owner_user_id='admin', slug='acme', domains=('acme.test',))
    return str(org.id)


def _scim(token: str = TOKEN) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _in_the_shared_plane() -> list[tuple]:
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.connect() as conn:
            return [
                tuple(r)
                for r in conn.execute(
                    text(
                        "SELECT method, route, actor_user_id, status_code FROM audit_events WHERE route LIKE '/scim/%'"
                    )
                ).all()
            ]
    finally:
        engine.dispose()


def _trail() -> list[tuple]:
    rows = [r for r in audit_rows() if r['route'].startswith('/scim/')]
    return sorted(
        (
            (r['method'], r['route'], r['actor_user_id'], r['org_id'], r['target_id'], r['detail'], r['status_code'])
            for r in rows
        ),
        key=lambda r: ('POST', 'PATCH', 'PUT', 'DELETE').index(r[0]),
    )


async def test_create_deactivate_reactivate_and_delete_are_each_recorded(client, org_id):
    created = await client.post(
        f'{SCIM}/Users',
        json={'schemas': [USER_SCHEMA], 'userName': 'sam@acme.test', 'active': True},
        headers=_scim(),
    )
    user_id = created.json()['id']
    await client.patch(
        f'{SCIM}/Users/{user_id}',
        json={'schemas': [PATCH_SCHEMA], 'Operations': [{'op': 'replace', 'path': 'active', 'value': False}]},
        headers=_scim(),
    )
    await client.put(
        f'{SCIM}/Users/{user_id}',
        json={'schemas': [USER_SCHEMA], 'userName': 'sam@acme.test', 'active': True},
        headers=_scim(),
    )
    await client.delete(f'{SCIM}/Users/{user_id}', headers=_scim())

    assert _trail() == [
        ('POST', '/scim/v2/Users', SCIM_ACTOR, org_id, user_id, {'active': True}, 201),
        ('PATCH', '/scim/v2/Users/{user_id}', SCIM_ACTOR, org_id, user_id, {'active': False}, 200),
        ('PUT', '/scim/v2/Users/{user_id}', SCIM_ACTOR, org_id, user_id, {'active': True}, 200),
        ('DELETE', '/scim/v2/Users/{user_id}', SCIM_ACTOR, org_id, user_id, {'active': False}, 204),
    ]
    # In the organisation's own trail, not the deployment's.
    assert _in_the_shared_plane() == []


async def test_a_provider_looking_somebody_up_is_not_an_event(client, org_id):
    await client.get(f'{SCIM}/Users?filter=userName eq "sam@acme.test"', headers=_scim())

    assert _trail() == []


async def test_a_wrong_token_is_recorded_in_the_deployments_trail(client, org_id):
    await client.delete(f'{SCIM}/Users/someone', headers=_scim('not-the-token'))

    assert _in_the_shared_plane() == [('DELETE', '/scim/v2/Users/{user_id}', None, 401)]


async def test_somebody_on_no_organisations_domain_is_in_the_deployments_trail(client, org_id):
    resp = await client.post(
        f'{SCIM}/Users',
        json={'schemas': [USER_SCHEMA], 'userName': 'stray@nowhere.test', 'active': True},
        headers=_scim(),
    )

    assert resp.status_code == 409
    assert _in_the_shared_plane() == [('POST', '/scim/v2/Users', SCIM_ACTOR, 409)]
