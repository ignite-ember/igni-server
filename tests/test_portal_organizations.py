"""Integration tests for portal organization endpoints."""

import uuid

from tests.conftest import add_member, auth_header, create_org, create_user, make_token


class TestListOrganizations:
    async def test_list_orgs_returns_the_one_they_are_in(self, client, db):
        """Owning a second organisation does not make you a member of it: one at a time."""
        await create_user(db, user_id='u1', email='a@test.com')
        org1 = await create_org(db, owner_user_id='u1', slug='org-a', display_name='Org A')
        await create_org(db, owner_user_id='u1', slug='org-b', display_name='Org B')

        token = make_token('u1', org_id=str(org1.id))
        resp = await client.get('/portal/organizations', headers=auth_header(token))

        assert resp.status_code == 200
        slugs = {o['slug'] for o in resp.json()}
        assert slugs == {'org-a'}

    async def test_list_orgs_excludes_other_users(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com')
        await create_user(db, user_id='u2', email='b@test.com')
        org1 = await create_org(db, owner_user_id='u1', slug='org-a', display_name='Org A')
        await create_org(db, owner_user_id='u2', slug='org-b', display_name='Org B')

        token = make_token('u1', org_id=str(org1.id))
        resp = await client.get('/portal/organizations', headers=auth_header(token))

        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]['slug'] == 'org-a'

    async def test_list_orgs_unauthenticated(self, client):
        resp = await client.get('/portal/organizations')
        assert resp.status_code == 401


class TestCreateOrganization:
    async def test_create_org(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com', is_super_admin=True)
        org = await create_org(db, owner_user_id='u1', slug='existing', display_name='Existing')
        token = make_token('u1', org_id=str(org.id))

        resp = await client.post(
            '/portal/organizations',
            json={'display_name': 'New Org', 'domains': ['neworg.io']},
            headers=auth_header(token),
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data['display_name'] == 'New Org'
        assert data['slug']  # auto-generated
        assert data['id']

    async def test_create_org_with_custom_slug(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com', is_super_admin=True)
        org = await create_org(db, owner_user_id='u1', slug='existing', display_name='Existing')
        token = make_token('u1', org_id=str(org.id))

        resp = await client.post(
            '/portal/organizations',
            json={'display_name': 'Custom', 'slug': 'my-custom-slug', 'domains': ['custom.io']},
            headers=auth_header(token),
        )

        assert resp.status_code == 201
        assert resp.json()['slug'] == 'my-custom-slug'

    async def test_create_org_invalid_slug(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com', is_super_admin=True)
        org = await create_org(db, owner_user_id='u1', slug='existing', display_name='Existing')
        token = make_token('u1', org_id=str(org.id))

        resp = await client.post(
            '/portal/organizations',
            json={'display_name': 'Bad Slug', 'slug': 'X!', 'domains': ['badslug.io']},
            headers=auth_header(token),
        )

        assert resp.status_code == 422


class TestTheDerivedSlugIsStillAValidSlug:
    """The derive path never went through `_validate_slug`.

    So the 3-32-character, no-edge-hyphen rule held only for slugs
    somebody typed, and a display name with nothing alphanumeric in it
    derived to the empty string — which `organizations.slug` accepts,
    being NOT NULL rather than non-empty, and which then collided with
    the next such org on the UNIQUE index.
    """

    async def _create(self, client, db, display_name: str, *, user: str):
        await create_user(db, user_id=user, email=f'{user}@test.com', is_super_admin=True)
        org = await create_org(db, owner_user_id=user, slug=f'seed-{user}', display_name='Seed')
        token = make_token(user, org_id=str(org.id))
        return await client.post(
            '/portal/organizations',
            json={'display_name': display_name, 'domains': [f'{uuid.uuid4().hex[:12]}.io']},
            headers=auth_header(token),
        )

    async def test_a_name_with_no_latin_characters_still_gets_a_slug(self, client, db):
        resp = await self._create(client, db, '日本語', user='u1')

        assert resp.status_code == 201, resp.text
        assert resp.json()['slug'], 'an empty slug was stored'

    async def test_a_name_too_short_to_derive_from_still_gets_a_valid_slug(self, client, db):
        """'Ab' derived to 'ab' — two characters, under the minimum the
        other path enforces."""
        resp = await self._create(client, db, 'Ab', user='u2')

        assert resp.status_code == 201, resp.text
        assert len(resp.json()['slug']) >= 3, resp.json()['slug']

    async def test_two_underivable_names_do_not_collide(self, client, db):
        """Both used to derive to '' and the second was told its slug
        was taken — for a name it never chose a slug for."""
        first = await self._create(client, db, '!!!', user='u3')
        second = await self._create(client, db, '???', user='u4')

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()['slug'] != second.json()['slug']

    async def test_a_derived_slug_passes_the_rule_the_typed_one_must(self, client, db):
        """The invariant itself, rather than the three ways it broke."""
        import re

        for i, name in enumerate(['Acme Corp', '  spaced  out  ', 'X' * 60, 'a-', '日本語', 'Ab']):
            resp = await self._create(client, db, name, user=f'd{i}')
            assert resp.status_code == 201, resp.text
            slug = resp.json()['slug']
            assert re.match(r'^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$', slug), f'{name!r} derived {slug!r}'

    async def test_an_ordinary_name_still_derives_the_readable_slug(self, client, db):
        """The fallback must not have replaced the normal behaviour."""
        resp = await self._create(client, db, 'Acme Corp', user='u5')

        assert resp.json()['slug'] == 'acme-corp'


class TestUpdateOrganization:
    async def test_rename_org_as_admin(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com')
        org = await create_org(db, owner_user_id='u1', slug='my-org', display_name='My Org')
        token = make_token('u1', org_id=str(org.id))

        resp = await client.patch(
            f'/portal/organizations/{org.id}',
            json={'display_name': 'Renamed Org'},
            headers=auth_header(token),
        )

        assert resp.status_code == 200
        assert resp.json()['display_name'] == 'Renamed Org'

    async def test_rename_org_as_member_forbidden(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com')
        await create_user(db, user_id='u2', email='b@test.com')
        org = await create_org(db, owner_user_id='u1', slug='my-org', display_name='My Org')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')

        token = make_token('u2', org_id=str(org.id))
        resp = await client.patch(
            f'/portal/organizations/{org.id}',
            json={'display_name': 'Nope'},
            headers=auth_header(token),
        )

        assert resp.status_code == 403

    async def test_rename_org_not_member_forbidden(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com')
        await create_user(db, user_id='u2', email='b@test.com')
        org = await create_org(db, owner_user_id='u1', slug='my-org', display_name='My Org')

        # u2 is not a member of this org at all
        other_org = await create_org(db, owner_user_id='u2', slug='other', display_name='Other')
        token = make_token('u2', org_id=str(other_org.id))

        resp = await client.patch(
            f'/portal/organizations/{org.id}',
            json={'display_name': 'Hacked'},
            headers=auth_header(token),
        )

        assert resp.status_code == 403


class TestDeleteOrganization:
    async def test_delete_org_as_sole_admin(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com')
        org1 = await create_org(db, owner_user_id='u1', slug='org-a', display_name='Org A')
        await create_org(db, owner_user_id='u1', slug='org-b', display_name='Org B')

        token = make_token('u1', org_id=str(org1.id))
        resp = await client.delete(
            f'/portal/organizations/{org1.id}',
            headers=auth_header(token),
        )

        assert resp.status_code == 200

    async def test_the_last_org_can_be_deleted_when_nobody_else_is_in_it(self, client, db):
        """It used to refuse your *only* organisation, because every
        account had one of its own. Being in none is where every account
        starts now."""
        await create_user(db, user_id='u1', email='a@test.com')
        org = await create_org(db, owner_user_id='u1', slug='only-org', display_name='Only Org')

        token = make_token('u1', org_id=str(org.id))
        resp = await client.delete(
            f'/portal/organizations/{org.id}',
            headers=auth_header(token),
        )

        assert resp.status_code == 200

    async def test_cannot_delete_org_with_other_members(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com')
        await create_user(db, user_id='u2', email='b@test.com')
        org1 = await create_org(db, owner_user_id='u1', slug='org-a', display_name='Org A')
        await create_org(db, owner_user_id='u1', slug='org-b', display_name='Org B')
        await add_member(db, org_id=str(org1.id), user_id='u2', role='member')

        token = make_token('u1', org_id=str(org1.id))
        resp = await client.delete(
            f'/portal/organizations/{org1.id}',
            headers=auth_header(token),
        )

        assert resp.status_code == 400
        assert 'member' in resp.json()['detail'].lower()

    async def test_member_cannot_delete_org(self, client, db):
        await create_user(db, user_id='u1', email='a@test.com')
        await create_user(db, user_id='u2', email='b@test.com')
        org = await create_org(db, owner_user_id='u1', slug='my-org', display_name='My Org')
        await add_member(db, org_id=str(org.id), user_id='u2', role='member')
        # u2 needs another org so "last org" check doesn't trigger first
        await create_org(db, owner_user_id='u2', slug='u2-org', display_name='U2 Org')

        token = make_token('u2', org_id=str(org.id))
        resp = await client.delete(
            f'/portal/organizations/{org.id}',
            headers=auth_header(token),
        )

        assert resp.status_code == 403
