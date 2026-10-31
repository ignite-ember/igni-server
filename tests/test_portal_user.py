"""Integration tests for portal user endpoints (/portal/me)."""

from tests.conftest import add_member, auth_header, create_org, create_user, make_token


class TestGetMe:
    async def test_get_me_returns_profile(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com', full_name='Alice')
        org = await create_org(db, owner_user_id='u1', slug='my-org', display_name='My Org')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.get('/portal/me', headers=auth_header(token))

        assert resp.status_code == 200
        data = resp.json()
        assert data['user_id'] == 'u1'
        assert data['email'] == 'a@test.com'
        assert data['full_name'] == 'Alice'
        assert data['org_id'] == str(org.id)
        assert data['role'] == 'admin'

    async def test_get_me_includes_their_one_org(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com')
        org1 = await create_org(db, owner_user_id='u1', slug='org-a', display_name='Org A')
        await create_org(db, owner_user_id='u1', slug='org-b', display_name='Org B')

        token = make_token('u1', org_id=str(org1.id))
        resp = await client.get('/portal/me', headers=auth_header(token))

        assert resp.status_code == 200
        orgs = resp.json()['organizations']
        assert [o['slug'] for o in orgs] == ['org-a']

    async def test_get_me_shows_member_role(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com')
        await create_user(db, user_id='u2', email='b@test.com', full_name='Bob')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')

        token = make_token('u2', org_id=str(org.id))
        resp = await client.get('/portal/me', headers=auth_header(token))

        assert resp.status_code == 200
        assert resp.json()['role'] == 'member'

    async def test_get_me_auth_provider_field(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com', auth_provider='google')
        org = await create_org(db, owner_user_id='u1', slug='my-org', display_name='My Org')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.get('/portal/me', headers=auth_header(token))

        assert resp.status_code == 200
        assert resp.json()['auth_provider'] == 'google'

    async def test_get_me_unauthenticated(self, client):
        resp = await client.get('/portal/me')
        assert resp.status_code == 401

    async def test_get_me_invalid_token(self, client):
        resp = await client.get('/portal/me', headers=auth_header('garbage-token'))
        assert resp.status_code == 401

    async def test_get_me_org_via_header(self, client, db):
        """Portal flow: org_id comes from X-Org-Id header, not token."""
        await create_user(db, user_id='u1', email='a@test.com')
        org = await create_org(db, owner_user_id='u1', slug='my-org', display_name='My Org')

        # Token without org claim, org selected via header
        token = make_token('u1')
        headers = {**auth_header(token), 'X-Org-Id': str(org.id)}
        resp = await client.get('/portal/me', headers=headers)

        assert resp.status_code == 200
        assert resp.json()['org_id'] == str(org.id)

    async def test_get_me_with_a_stale_org_header_falls_back(self, client, db):
        """A remembered X-Org-Id naming an organisation the account is not
        in — it was deleted, or they were removed — must not lock them
        out of /me, the page that lets them pick another. Org-scoped
        routes still refuse it (tests/test_a_person_belongs_to_their_domains_organisation.py)."""
        await create_user(db, user_id='u1', email='a@test.com')
        await create_org(db, owner_user_id='u1', slug='my-org', display_name='My Org')

        token = make_token('u1')
        headers = {**auth_header(token), 'X-Org-Id': 'nonexistent-org-id'}
        resp = await client.get('/portal/me', headers=headers)

        assert resp.status_code == 200
        assert resp.json()['org_slug'] == 'my-org'


class TestDeleteAccount:
    async def test_delete_account_sole_member(self, client, db):
        """User is sole member of their only org — both get deleted."""
        await create_user(db, user_id='u1', email='a@test.com')
        org = await create_org(db, owner_user_id='u1', slug='my-org', display_name='My Org')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.delete('/portal/me', headers=auth_header(token))

        assert resp.status_code == 200
        assert resp.json()['message'] == 'Account deleted'

    async def test_delete_account_removes_from_the_org(self, client, db):
        """User is a member (not admin) of somebody else's org — membership removed."""
        await create_user(db, user_id='u1', email='a@test.com')
        await create_user(db, user_id='u2', email='b@test.com')
        # u1 is a member of u2's org
        org2 = await create_org(db, owner_user_id='u2', slug='u2-org', display_name='U2 Org')
        await add_member(db, org_id=str(org2.id), user_id='u1', role='member')

        token = make_token('u1', org_id=str(org2.id))
        resp = await client.delete('/portal/me', headers=auth_header(token))

        assert resp.status_code == 200

        # Verify u2's org still exists and u2 can access it
        token2 = make_token('u2', org_id=str(org2.id))
        resp2 = await client.get('/portal/organizations', headers=auth_header(token2))
        assert resp2.status_code == 200
        assert any(o['slug'] == 'u2-org' for o in resp2.json())

    async def test_delete_account_blocked_if_sole_admin_with_members(self, client, db):
        """Can't delete if you're the only admin in an org that has other members."""
        await create_user(db, user_id='u1', email='a@test.com')
        await create_user(db, user_id='u2', email='b@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.delete('/portal/me', headers=auth_header(token))

        assert resp.status_code == 400
        assert 'transfer admin' in resp.json()['detail'].lower()

    async def test_delete_account_ok_if_another_admin_exists(self, client, db):
        """Can delete if there's another admin to take over."""
        await create_user(db, user_id='u1', email='a@test.com')
        await create_user(db, user_id='u2', email='b@test.com')
        org = await create_org(db, owner_user_id='u1', slug='team', display_name='Team')
        await add_member(db, org_id=str(org.id), user_id='u2', role='admin')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.delete('/portal/me', headers=auth_header(token))

        assert resp.status_code == 200
