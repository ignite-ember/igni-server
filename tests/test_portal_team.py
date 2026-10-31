"""Integration tests for portal team endpoints."""

from tests.conftest import add_member, auth_header, create_org, create_user, make_token


class TestListTeamMembers:
    async def test_list_members(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com', full_name='Alice')
        await create_user(db, user_id='u2', email='b@test.com', full_name='Bob')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.get('/portal/team', headers=auth_header(token))

        assert resp.status_code == 200
        data = resp.json()
        assert data['total'] == 2
        emails = {m['email'] for m in data['members']}
        assert 'a@test.com' in emails
        assert 'b@test.com' in emails

    async def test_list_members_unauthenticated(self, client):
        resp = await client.get('/portal/team')
        assert resp.status_code == 401


class TestInviteMember:
    async def test_invite_member_as_admin(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))

        token = make_token('u1', org_id=str(org.id))
        resp = await client.post(
            '/portal/team/invite',
            json={'email': 'newbie@test.com', 'role': 'member'},
            headers=auth_header(token),
        )

        assert resp.status_code in (200, 201)

    async def test_invite_member_as_non_admin_forbidden(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')

        token = make_token('u2', org_id=str(org.id))
        resp = await client.post(
            '/portal/team/invite',
            json={'email': 'newbie@test.com', 'role': 'member'},
            headers=auth_header(token),
        )

        assert resp.status_code == 403

    async def test_invite_duplicate_email(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))

        token = make_token('u1', org_id=str(org.id))
        # First invite succeeds
        resp1 = await client.post(
            '/portal/team/invite',
            json={'email': 'newbie@test.com', 'role': 'member'},
            headers=auth_header(token),
        )
        assert resp1.status_code in (200, 201)

        # Second invite to same email should conflict
        resp = await client.post(
            '/portal/team/invite',
            json={'email': 'newbie@test.com', 'role': 'member'},
            headers=auth_header(token),
        )

        assert resp.status_code == 409

    async def test_invite_invalid_role_rejected(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))

        token = make_token('u1', org_id=str(org.id))
        resp = await client.post(
            '/portal/team/invite',
            json={'email': 'newbie@test.com', 'role': 'superuser'},
            headers=auth_header(token),
        )

        # Pydantic rejects the literal at the schema layer (422), not the service.
        assert resp.status_code == 422


class TestUpdateMemberRole:
    async def test_change_role(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.patch(
            '/portal/team/u2/role',
            json={'role': 'admin'},
            headers=auth_header(token),
        )

        assert resp.status_code == 200

    async def test_cannot_change_own_role(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))

        token = make_token('u1', org_id=str(org.id))
        resp = await client.patch(
            '/portal/team/u1/role',
            json={'role': 'member'},
            headers=auth_header(token),
        )

        assert resp.status_code == 400
        assert 'own role' in resp.json()['detail'].lower()

    async def test_change_role_member_not_found(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))

        token = make_token('u1', org_id=str(org.id))
        resp = await client.patch(
            '/portal/team/ghost-user/role',
            json={'role': 'admin'},
            headers=auth_header(token),
        )

        assert resp.status_code == 404

    async def test_change_role_as_non_admin_forbidden(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        await create_user(db, user_id='u3', email='target@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')
        await add_member(db, org_id=str(org.id), user_id='u3', role='member')

        token = make_token('u2', org_id=str(org.id))
        resp = await client.patch(
            '/portal/team/u3/role',
            json={'role': 'admin'},
            headers=auth_header(token),
        )

        assert resp.status_code == 403


class TestUpdateMemberStatus:
    async def test_deactivate_member_as_admin(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.patch(
            '/portal/team/u2/status',
            json={'status': 'deactivated'},
            headers=auth_header(token),
        )

        assert resp.status_code == 200
        assert resp.json()['new_status'] == 'deactivated'

    async def test_cannot_change_own_status(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))

        token = make_token('u1', org_id=str(org.id))
        resp = await client.patch(
            '/portal/team/u1/status',
            json={'status': 'deactivated'},
            headers=auth_header(token),
        )

        assert resp.status_code == 400
        assert 'own status' in resp.json()['detail'].lower()

    async def test_cannot_reactivate_invited_member(self, client, db):
        # Invited members must accept the invite via signup; admin cannot
        # short-circuit by flipping them to active through this endpoint.
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))
        await add_member(db, org_id=str(org.id), user_id='invited-1', role='member', status='invited')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.patch(
            '/portal/team/invited-1/status',
            json={'status': 'active'},
            headers=auth_header(token),
        )

        assert resp.status_code == 400
        assert 'invited' in resp.json()['detail'].lower()

    async def test_can_reactivate_deactivated_member(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))
        await add_member(db, org_id=str(org.id), user_id='u2', role='member', status='deactivated')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.patch(
            '/portal/team/u2/status',
            json={'status': 'active'},
            headers=auth_header(token),
        )

        assert resp.status_code == 200
        assert resp.json()['new_status'] == 'active'


class TestRemoveMember:
    async def test_remove_member_as_admin(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.delete(
            '/portal/team/u2',
            headers=auth_header(token),
        )

        assert resp.status_code == 204

    async def test_cannot_remove_self(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))

        token = make_token('u1', org_id=str(org.id))
        resp = await client.delete(
            '/portal/team/u1',
            headers=auth_header(token),
        )

        assert resp.status_code == 400
        assert 'yourself' in resp.json()['detail'].lower()

    async def test_remove_member_not_found(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))

        token = make_token('u1', org_id=str(org.id))
        resp = await client.delete(
            '/portal/team/ghost-user',
            headers=auth_header(token),
        )

        assert resp.status_code == 404

    async def test_remove_member_as_non_admin_forbidden(self, client, db):
        await create_user(db, user_id='u1', email='admin@test.com')
        await create_user(db, user_id='u2', email='member@test.com')
        await create_user(db, user_id='u3', email='target@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team', domains=('test.com',))
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')
        await add_member(db, org_id=str(org.id), user_id='u3', role='member')

        token = make_token('u2', org_id=str(org.id))
        resp = await client.delete(
            '/portal/team/u3',
            headers=auth_header(token),
        )

        assert resp.status_code == 403
