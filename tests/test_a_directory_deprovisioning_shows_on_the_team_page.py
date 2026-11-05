"""SCIM's `active` does what an org admin's deactivate and reactivate do.

A person their identity provider deprovisioned was refused everywhere
and still listed as active on the Team page: SCIM set the account and
the page reads the membership. The portal's own deactivation sets both,
so the two levers disagreed about the same person. They share one path
now (`MemberStatus`): the membership, the account — which withdraws the
person's sessions — and their VM credentials.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.db.sql.user import UserDB
from tests.conftest import auth_header, create_org, create_user, make_token

SCIM = 'http://test/scim/v2'
TOKEN = 'scim-token-for-tests'
USER_SCHEMA = 'urn:ietf:params:scim:schemas:core:2.0:User'
PATCH_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:PatchOp'


@pytest.fixture(autouse=True)
def _scim_configured(monkeypatch):
    monkeypatch.setattr(settings, 'scim_token', TOKEN)


def _scim() -> dict:
    return {'Authorization': f'Bearer {TOKEN}'}


def _active(value: bool) -> dict:
    return {'schemas': [PATCH_SCHEMA], 'Operations': [{'op': 'replace', 'path': 'active', 'value': value}]}


async def _team_status(client, *, org_id: str, user_id: str) -> str:
    resp = await client.get('/portal/team', headers=auth_header(make_token('admin', org_id=org_id)))
    assert resp.status_code == 200
    return next(m['status'] for m in resp.json()['members'] if m['user_id'] == user_id)


async def _provisioned(client, address: str, *, active: bool = True) -> str:
    resp = await client.post(
        f'{SCIM}/Users',
        json={'schemas': [USER_SCHEMA], 'userName': address, 'displayName': 'Sam Rivera', 'active': active},
        headers=_scim(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()['id']


@pytest.fixture
async def org_id(db) -> str:
    await create_user(db, user_id='admin', email='admin@acme.test')
    org = await create_org(db, owner_user_id='admin', slug='acme', domains=('acme.test',))
    return str(org.id)


async def test_deprovisioning_shows_as_deactivated_on_the_team_page(client, org_id):
    user_id = await _provisioned(client, 'sam@acme.test')
    assert await _team_status(client, org_id=org_id, user_id=user_id) == 'active'

    resp = await client.patch(f'{SCIM}/Users/{user_id}', json=_active(False), headers=_scim())

    assert (resp.status_code, resp.json()['active']) == (200, False)
    assert await _team_status(client, org_id=org_id, user_id=user_id) == 'deactivated'


async def test_reprovisioning_shows_as_active_again(client, org_id):
    user_id = await _provisioned(client, 'sam@acme.test')
    await client.patch(f'{SCIM}/Users/{user_id}', json=_active(False), headers=_scim())

    resp = await client.patch(f'{SCIM}/Users/{user_id}', json=_active(True), headers=_scim())

    assert (resp.status_code, resp.json()['active']) == (200, True)
    assert await _team_status(client, org_id=org_id, user_id=user_id) == 'active'
    assert (await UserDB().get_by_user_id(user_id)).deactivated_at is None  # type: ignore[union-attr]


async def test_a_delete_shows_as_deactivated_too(client, org_id):
    user_id = await _provisioned(client, 'sam@acme.test')

    resp = await client.delete(f'{SCIM}/Users/{user_id}', headers=_scim())

    assert resp.status_code == 204
    assert await _team_status(client, org_id=org_id, user_id=user_id) == 'deactivated'


async def test_somebody_provisioned_inactive_is_listed_deactivated(client, org_id):
    user_id = await _provisioned(client, 'leaver@acme.test', active=False)

    assert await _team_status(client, org_id=org_id, user_id=user_id) == 'deactivated'


async def test_it_is_the_portals_deactivation(client, org_id, monkeypatch):
    """Sessions withdrawn and VM credentials revoked, as when an org admin deactivates."""
    revoked: list[str] = []

    async def revoke_person(self, user_id: str, *, org_ids: list[str] | None = None) -> None:
        revoked.append(user_id)

    monkeypatch.setattr('app.services.cloud.vm_credentials.VmCredentials.revoke_person', revoke_person)
    user_id = await _provisioned(client, 'sam@acme.test')
    token = make_token(user_id, org_id=org_id)
    assert (await client.get('/portal/me', headers=auth_header(token))).status_code == 200

    await client.patch(f'{SCIM}/Users/{user_id}', json=_active(False), headers=_scim())

    assert revoked == [user_id]
    assert (await client.get('/portal/me', headers=auth_header(token))).status_code == 401
    assert (await UserDB().get_by_user_id(user_id)).tokens_valid_after is not None  # type: ignore[union-attr]
