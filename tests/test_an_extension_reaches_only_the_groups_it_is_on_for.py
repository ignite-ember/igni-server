"""An approved extension reaches nobody until an org admin switches it on for a group.

A super admin approving a service is trust; it says nothing about who
uses it. That is the org admin's, per group, and it is off until they
decide (`docs/service-identity.md` §8.3). A person is in exactly one
group; a machine is listed, and served the bundle and assets of, only the
extensions on for it — the floor never hands a producer what it cannot use.
"""

from __future__ import annotations

import pytest

from app.db.sql.groups import GroupsDB
from app.db.sql.models.org_group_extension import OrgGroupExtension
from app.db.sql.tenancy import TenantSessions
from app.services.auth.jwt_service import JWTService
from tests.conftest import add_member, auth_header, create_org, create_user, make_token
from tests.registered_service import ExtensionBundle, RegisteredService

pytestmark = pytest.mark.anyio

EXTENSION = 'igni.codeindex'


async def _shipped(client, extension_id: str = EXTENSION, *, state: str = 'approved') -> RegisteredService:
    service = RegisteredService(base_url=f'https://{extension_id.split(".")[-1]}.example.com')
    await service.approve(state=state)
    bundle = ExtensionBundle.of({'id': extension_id, 'version': '1.0.0', 'name': extension_id})
    response = await client.put(
        f'/clients/{service.client_id}/extension',
        content=bundle.archive,
        headers={
            'Content-Type': 'application/gzip',
            'X-Igni-Bundle-Signature': bundle.signed_by(service, extension=extension_id, version='1.0.0'),
        },
    )
    assert response.status_code == 200, response.text
    return service


class Org:
    """An organisation with an admin, and people put in groups through the portal."""

    def __init__(self, client, db) -> None:
        self.client = client
        self.db = db
        self.org_id = ''
        self.admin: dict[str, str] = {}

    async def create(self) -> Org:
        owner = await create_user(self.db, email='admin@example.com')
        org = await create_org(self.db, owner_user_id=str(owner.user_id), slug='acme', display_name='Acme')
        self.org_id = str(org.id)
        self.admin = auth_header(make_token(str(owner.user_id), org_id=self.org_id))
        return self

    async def group(self, name: str) -> str:
        response = await self.client.post('/portal/groups', json={'name': name}, headers=self.admin)
        assert response.status_code == 201, response.text
        return str(response.json()['id'])

    async def person(self, email: str, group_id: str | None = None) -> tuple[str, dict[str, str]]:
        user = await create_user(self.db, email=email)
        user_id = str(user.user_id)
        await add_member(self.db, self.org_id, user_id)
        if group_id is not None:
            response = await self.client.post(
                f'/portal/groups/{group_id}/members', json={'user_id': user_id}, headers=self.admin
            )
            assert response.status_code == 200, response.text
        return user_id, auth_header(make_token(user_id, org_id=self.org_id))

    async def switch(self, group_id: str, extension_id: str, enabled: bool) -> int:
        response = await self.client.put(
            f'/portal/groups/{group_id}/extensions/{extension_id}', json={'enabled': enabled}, headers=self.admin
        )
        return response.status_code


async def _listed(client, headers: dict[str, str]) -> dict[str, object]:
    response = await client.get('/extensions', headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _ids(listing: dict[str, object]) -> list[str]:
    return [str(entry['extension_id']) for entry in listing['extensions']]  # type: ignore[index, union-attr]


class TestOffUntilAnAdminSwitchesItOn:
    async def test_an_approved_extension_reaches_nobody_by_default(self, client, db):
        await _shipped(client)
        org = await Org(client, db).create()
        legal = await org.group('Legal')
        _, person = await org.person('someone@example.com', legal)

        assert _ids(await _listed(client, person)) == []
        assert (await client.get(f'/extensions/{EXTENSION}/bundle', headers=person)).status_code == 404

    async def test_switched_on_for_their_group_it_reaches_them(self, client, db):
        await _shipped(client)
        org = await Org(client, db).create()
        legal = await org.group('Legal')
        _, person = await org.person('someone@example.com', legal)

        assert await org.switch(legal, EXTENSION, True) == 204
        listing = await _listed(client, person)

        assert _ids(listing) == [EXTENSION]
        assert listing['group'] == legal
        assert listing['extensions'][0]['groups'] == [legal]  # type: ignore[index]
        assert (await client.get(f'/extensions/{EXTENSION}/bundle', headers=person)).status_code == 200

    async def test_another_groups_extension_never_reaches_them(self, client, db):
        await _shipped(client)
        org = await Org(client, db).create()
        legal, finance = await org.group('Legal'), await org.group('Finance')
        _, lawyer = await org.person('lawyer@example.com', legal)
        _, accountant = await org.person('accountant@example.com', finance)

        await org.switch(legal, EXTENSION, True)

        assert _ids(await _listed(client, lawyer)) == [EXTENSION]
        assert _ids(await _listed(client, accountant)) == []
        assert (await client.get(f'/extensions/{EXTENSION}/bundle', headers=accountant)).status_code == 404

    async def test_switched_off_it_is_gone(self, client, db):
        await _shipped(client)
        org = await Org(client, db).create()
        legal = await org.group('Legal')
        _, person = await org.person('someone@example.com', legal)
        await org.switch(legal, EXTENSION, True)

        assert await org.switch(legal, EXTENSION, False) == 204

        assert _ids(await _listed(client, person)) == []

    async def test_the_listing_says_every_group_it_is_on_for(self, client, db):
        await _shipped(client)
        org = await Org(client, db).create()
        legal, finance = await org.group('Legal'), await org.group('Finance')
        _, person = await org.person('someone@example.com', legal)
        await org.switch(legal, EXTENSION, True)
        await org.switch(finance, EXTENSION, True)

        groups = (await _listed(client, person))['extensions'][0]['groups']  # type: ignore[index]

        assert sorted(groups) == sorted([legal, finance])

    async def test_the_priority_order_names_only_what_they_get(self, client, db):
        await _shipped(client)
        await _shipped(client, 'acme.lint')
        org = await Org(client, db).create()
        legal = await org.group('Legal')
        _, person = await org.person('someone@example.com', legal)
        await org.switch(legal, 'acme.lint', True)

        assert (await _listed(client, person))['priority'] == ['acme.lint']

    async def test_somebody_in_no_group_gets_the_default_groups(self, client, db):
        """They are put in the Default group, as the group pack does, and it can have extensions too."""
        await _shipped(client)
        org = await Org(client, db).create()
        user_id, person = await org.person('newcomer@example.com')

        assert _ids(await _listed(client, person)) == []
        default = await GroupsDB().get_user_group_summary(user_id)
        assert default is not None
        assert await org.switch(str(default.id), EXTENSION, True) == 204
        assert _ids(await _listed(client, person)) == [EXTENSION]


class TestTheGroupPage:
    async def test_it_lists_every_approved_extension_with_its_switch(self, client, db):
        await _shipped(client)
        await _shipped(client, 'acme.lint')
        await _shipped(client, 'acme.pending', state='pending')
        org = await Org(client, db).create()
        legal = await org.group('Legal')
        await org.switch(legal, 'acme.lint', True)

        response = await client.get(f'/portal/groups/{legal}/extensions', headers=org.admin)

        assert response.status_code == 200, response.text
        listed = {entry['extension_id']: entry['enabled'] for entry in response.json()['extensions']}
        assert listed == {EXTENSION: False, 'acme.lint': True}

    async def test_only_an_approved_extension_can_be_switched_on(self, client, db):
        await _shipped(client, 'acme.pending', state='pending')
        org = await Org(client, db).create()
        legal = await org.group('Legal')

        assert await org.switch(legal, 'acme.pending', True) == 404
        assert await org.switch(legal, 'acme.unknown', True) == 404

    async def test_only_an_org_admin_switches(self, client, db):
        await _shipped(client)
        org = await Org(client, db).create()
        legal = await org.group('Legal')
        _, member = await org.person('someone@example.com', legal)

        response = await client.put(
            f'/portal/groups/{legal}/extensions/{EXTENSION}', json={'enabled': True}, headers=member
        )

        assert response.status_code == 403

    async def test_another_orgs_group_is_not_found(self, client, db):
        await _shipped(client)
        org = await Org(client, db).create()
        stranger = await create_user(db, email='other-admin@example.com')
        other = await create_org(db, owner_user_id=str(stranger.user_id), slug='other', display_name='Other')
        theirs = await client.post(
            '/portal/groups',
            json={'name': 'Theirs'},
            headers=auth_header(make_token(str(stranger.user_id), org_id=str(other.id))),
        )

        assert await org.switch(str(theirs.json()['id']), EXTENSION, True) == 404


class TestOneGroupDecides:
    async def test_moving_them_changes_what_they_get(self, client, db):
        await _shipped(client)
        org = await Org(client, db).create()
        legal, finance = await org.group('Legal'), await org.group('Finance')
        user_id, person = await org.person('someone@example.com', legal)
        await org.switch(legal, EXTENSION, True)
        assert _ids(await _listed(client, person)) == [EXTENSION]

        moved = await client.post(f'/portal/groups/{finance}/members', json={'user_id': user_id}, headers=org.admin)
        assert moved.status_code == 200, moved.text

        listing = await _listed(client, person)
        assert listing['group'] == finance
        assert _ids(listing) == []
        assert (await client.get(f'/extensions/{EXTENSION}/bundle', headers=person)).status_code == 404

    async def test_taken_out_of_their_group_they_get_the_default_groups(self, client, db):
        await _shipped(client)
        org = await Org(client, db).create()
        legal = await org.group('Legal')
        user_id, person = await org.person('someone@example.com', legal)
        await org.switch(legal, EXTENSION, True)

        removed = await client.delete(f'/portal/groups/{legal}/members/{user_id}', headers=org.admin)
        assert removed.status_code == 204, removed.text

        listing = await _listed(client, person)
        default = await GroupsDB().get_user_group_summary(user_id)
        assert default is not None
        assert listing['group'] == str(default.id) != legal
        assert _ids(listing) == []


class TestTheSessionTokenCarriesTheirGroup:
    async def test_the_cli_token_names_their_group(self, client, db):
        org = await Org(client, db).create()
        legal = await org.group('Legal')
        user_id, person = await org.person('someone@example.com', legal)

        response = await client.post('/cli/token', headers=person, json={'org_id': org.org_id})

        assert response.status_code == 200, response.text
        claims = JWTService().decode_token(response.json()['access_token'])
        assert claims.user_id == user_id
        assert claims.group == legal

    async def test_somebody_in_no_group_yet_gets_the_default_group_in_their_token(self, client, db):
        org = await Org(client, db).create()
        user_id, person = await org.person('newcomer@example.com')

        response = await client.post('/cli/token', headers=person, json={'org_id': org.org_id})

        claims = JWTService().decode_token(response.json()['access_token'])
        default = await GroupsDB().get_user_group_summary(user_id)
        assert default is not None
        assert claims.group == str(default.id)


class TestForgettingAnAdmin:
    async def test_who_switched_it_on_is_cleared(self, client, db):
        await _shipped(client)
        org = await Org(client, db).create()
        legal = await org.group('Legal')
        user_id, _ = await org.person('helper@example.com')
        await add_member(db, org.org_id, user_id, role='admin')
        helper = auth_header(make_token(user_id, org_id=org.org_id))
        await client.put(f'/portal/groups/{legal}/extensions/{EXTENSION}', json={'enabled': True}, headers=helper)

        await GroupsDB().forget_person(org.org_id, user_id)

        async with TenantSessions().session_scope(org.org_id) as session:
            row = await session.get(OrgGroupExtension, await _row_id(org.org_id, legal))
            assert row is not None
            assert row.enabled_by_user_id is None


async def _row_id(org_id: str, group_id: str) -> str:
    async with TenantSessions().session_scope(org_id) as session:
        found = await session.execute(
            OrgGroupExtension.__table__.select().where(OrgGroupExtension.group_id == group_id)  # type: ignore[attr-defined]
        )
        return str(found.first().id)
