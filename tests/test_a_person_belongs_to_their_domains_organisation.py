"""A person belongs to the organisation that owns their address's domain.

A self-hosted, corporate install holds one or two organisations, and its
people are the company's. So:

* an organisation owns email domains, and no two share one;
* an account is only made for an address on a claimed domain, and it
  joins that organisation as it is made — claiming a pending invitation,
  or as admin when the organisation has none yet;
* a fresh install's first account may use any company address. It is
  the bootstrap account and a super admin. It creates the organisation,
  then moves onto an address on its domain or makes in-domain super
  admins and deletes itself;
* only super admins create organisations, change their domains, make
  other super admins, and change deployment-wide settings; the last
  super admin cannot be removed;
* the directory (SCIM) follows the same rule;
* nothing crosses organisations.

Apps (a PR reviewer and the like) are a different kind of identity and
are not accounts here.
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, status
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.user import User
from app.db.sql.schemas.org_domain import NotACompanyAddress, NotSetUpYet
from app.db.sql.user import LastSuperAdmin, UserDB
from app.security.portal_auth import NO_ORGANISATION
from app.services.auth.otp_service import OTPResult, OTPService
from tests.conftest import add_member, auth_header, create_org, create_user, make_token

_HASH = '$2b$04$' + 'a' * 53


@pytest.fixture
def codes_accepted(monkeypatch):
    """Sign-up and address codes that always check out: these tests are about who, not codes."""

    async def _accept(email, code):
        del email, code

    async def _issue(email):
        return OTPResult(message=f'Verification code has been sent to {email}', code='123456', email=email)

    async def _check_address(email, code):
        del email
        if code != '123456':
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid code')

    monkeypatch.setattr(OTPService, 'validate_signup_code', staticmethod(_accept))
    monkeypatch.setattr(OTPService, 'generate_address_code', staticmethod(_issue))
    monkeypatch.setattr(OTPService, 'validate_address_code', staticmethod(_check_address))


async def _sign_up(client, email: str):
    return await client.post(
        '/sign-up',
        json={'first_name': 'New', 'last_name': 'Person', 'email': email, 'code': '123456', 'password': 'Secure@pass1'},
    )


async def _signed_up(client, email: str) -> str:
    response = await _sign_up(client, email)
    assert response.status_code == 201, response.text
    user = await UserDB().get_by_email(email)
    assert user is not None
    return str(user.user_id)


async def _memberships(db, user_id: str) -> list[tuple[str, str]]:
    result = await db.execute(
        select(OrgMembership.org_id, OrgMembership.role).where(
            OrgMembership.user_id == user_id, OrgMembership.status == 'active'
        )
    )
    return [(str(org), str(role)) for org, role in result.all()]


async def _super_admin(db, email: str) -> User:
    return await create_user(db, email=email, is_super_admin=True)


class TestAFreshInstall:
    async def test_the_first_account_is_a_super_admin_in_no_organisation(self, client, db, codes_accepted):
        user_id = await _signed_up(client, 'founder@acmecorp.io')

        assert (await UserDB().get_by_user_id(user_id)).is_super_admin is True
        assert await _memberships(db, user_id) == []

    async def test_a_second_account_waits_for_the_organisation(self, client, codes_accepted):
        await _signed_up(client, 'founder@acmecorp.io')

        response = await _sign_up(client, 'second@acmecorp.io')

        assert response.status_code == 403
        assert response.json()['detail'] == str(NotSetUpYet())

    async def test_two_first_sign_ups_at_once_make_one_account(self, db):
        outcomes = await asyncio.gather(
            *(UserDB().create(email=f'racer{i}@acmecorp.io', hashed_password=_HASH) for i in range(6)),
            return_exceptions=True,
        )

        made = [o for o in outcomes if isinstance(o, User)]
        assert len(made) == 1 and made[0].is_super_admin is True
        assert all(isinstance(o, NotSetUpYet) for o in outcomes if o not in made)

    async def test_its_pages_say_why_until_it_has_an_organisation(self, client, codes_accepted):
        user_id = await _signed_up(client, 'founder@acmecorp.io')
        headers = auth_header(make_token(user_id))

        me = await client.get('/portal/me', headers=headers)
        team = await client.get('/portal/team', headers=headers)

        assert me.status_code == 200 and me.json()['organizations'] == []
        assert team.status_code == 403 and team.json()['detail'] == NO_ORGANISATION


class TestTheDomainDecides:
    async def test_somebody_on_a_claimed_domain_joins_it(self, client, db, codes_accepted):
        owner = await create_user(db, email='owner@acmecorp.io')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug='acme', domains=('acmecorp.io',))

        user_id = await _signed_up(client, 'new@acmecorp.io')

        assert await _memberships(db, user_id) == [(str(org.id), 'member')]

    async def test_the_first_to_join_an_organisation_with_no_admin_administers_it(self, client, db, codes_accepted):
        boss = await _super_admin(db, 'boss@acmecorp.io')
        await create_org(db, owner_user_id=str(boss.user_id), slug='acme', domains=('acmecorp.io',))
        created = await client.post(
            '/portal/organizations',
            json={'display_name': 'Beta', 'domains': ['beta-corp.io']},
            headers=auth_header(make_token(str(boss.user_id))),
        )
        assert created.status_code == 201, created.text

        first = await _signed_up(client, 'one@beta-corp.io')
        second = await _signed_up(client, 'two@beta-corp.io')

        assert await _memberships(db, first) == [(created.json()['id'], 'admin')]
        assert await _memberships(db, second) == [(created.json()['id'], 'member')]

    async def test_an_address_on_no_claimed_domain_is_refused(self, client, db, codes_accepted):
        owner = await create_user(db, email='owner@acmecorp.io')
        await create_org(db, owner_user_id=str(owner.user_id), slug='acme', domains=('acmecorp.io',))

        response = await _sign_up(client, 'outsider@elsewhere.io')

        assert response.status_code == 403
        assert response.json()['detail'] == str(NotACompanyAddress())

    async def test_an_invitation_sets_the_role_they_join_with(self, client, db, codes_accepted):
        owner = await create_user(db, email='owner@acmecorp.io')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug='acme', domains=('acmecorp.io',))
        invited = await client.post(
            '/portal/team/invite',
            json={'email': 'lead@acmecorp.io', 'role': 'admin'},
            headers={**auth_header(make_token(str(owner.user_id))), 'X-Org-Id': str(org.id)},
        )
        assert invited.status_code in (200, 201), invited.text

        user_id = await _signed_up(client, 'lead@acmecorp.io')

        assert await _memberships(db, user_id) == [(str(org.id), 'admin')]

    async def test_an_invitation_off_the_domains_is_refused(self, client, db):
        owner = await create_user(db, email='owner@acmecorp.io')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug='acme', domains=('acmecorp.io',))

        response = await client.post(
            '/portal/team/invite',
            json={'email': 'friend@elsewhere.io', 'role': 'member'},
            headers={**auth_header(make_token(str(owner.user_id))), 'X-Org-Id': str(org.id)},
        )

        assert response.status_code == 422
        assert 'acmecorp.io' in response.json()['detail']


class TestOnlySuperAdminsCreateOrganisations:
    async def test_an_org_admin_who_is_not_one_cannot(self, client, db):
        owner = await create_user(db, email='owner@acmecorp.io')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug='acme', domains=('acmecorp.io',))

        response = await client.post(
            '/portal/organizations',
            json={'display_name': 'Another', 'domains': ['another.io']},
            headers={**auth_header(make_token(str(owner.user_id))), 'X-Org-Id': str(org.id)},
        )

        assert response.status_code == 403

    async def test_the_creator_joins_only_if_the_domain_is_theirs(self, client, db):
        boss = await _super_admin(db, 'boss@acmecorp.io')

        response = await client.post(
            '/portal/organizations',
            json={'display_name': 'Acme', 'domains': ['acmecorp.io', 'acme-uk.io']},
            headers=auth_header(make_token(str(boss.user_id))),
        )

        assert response.status_code == 201, response.text
        assert await _memberships(db, str(boss.user_id)) == [(response.json()['id'], 'admin')]

    async def test_a_domain_cannot_belong_to_two(self, client, db):
        boss = await _super_admin(db, 'boss@acmecorp.io')
        await create_org(db, owner_user_id=str(boss.user_id), slug='acme', domains=('acmecorp.io',))

        response = await client.post(
            '/portal/organizations',
            json={'display_name': 'Copy', 'domains': ['acmecorp.io']},
            headers=auth_header(make_token(str(boss.user_id))),
        )

        assert response.status_code == 409

    @pytest.mark.parametrize('domains', [[], ['gmail.com'], ['not a domain']])
    async def test_it_needs_company_domains(self, client, db, domains):
        boss = await _super_admin(db, 'boss@acmecorp.io')

        response = await client.post(
            '/portal/organizations',
            json={'display_name': 'Acme', 'domains': domains},
            headers=auth_header(make_token(str(boss.user_id))),
        )

        assert response.status_code == 422


class TestDomainsChangeUnderASuperAdmin:
    async def test_adding_one(self, client, db):
        boss = await _super_admin(db, 'boss@acmecorp.io')
        org = await create_org(db, owner_user_id=str(boss.user_id), slug='acme', domains=('acmecorp.io',))

        response = await client.post(
            f'/portal/organizations/{org.id}/domains',
            json={'domain': 'Acme-UK.io'},
            headers=auth_header(make_token(str(boss.user_id))),
        )

        assert response.status_code == 200, response.text
        assert response.json()['domains'] == ['acme-uk.io', 'acmecorp.io']

    async def test_removing_one_somebody_uses_is_refused(self, client, db):
        boss = await _super_admin(db, 'boss@acmecorp.io')
        org = await create_org(db, owner_user_id=str(boss.user_id), slug='acme', domains=('acmecorp.io',))

        response = await client.delete(
            f'/portal/organizations/{org.id}/domains/acmecorp.io',
            headers=auth_header(make_token(str(boss.user_id))),
        )

        assert response.status_code == 409

    async def test_removing_an_unused_one(self, client, db):
        boss = await _super_admin(db, 'boss@acmecorp.io')
        org = await create_org(db, owner_user_id=str(boss.user_id), slug='acme', domains=('acmecorp.io', 'acme-uk.io'))

        response = await client.delete(
            f'/portal/organizations/{org.id}/domains/acme-uk.io',
            headers=auth_header(make_token(str(boss.user_id))),
        )

        assert response.status_code == 200, response.text
        assert response.json()['domains'] == ['acmecorp.io']

    async def test_nobody_else_may(self, client, db):
        owner = await create_user(db, email='owner@acmecorp.io')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug='acme', domains=('acmecorp.io',))

        response = await client.post(
            f'/portal/organizations/{org.id}/domains',
            json={'domain': 'acme-uk.io'},
            headers={**auth_header(make_token(str(owner.user_id))), 'X-Org-Id': str(org.id)},
        )

        assert response.status_code == 403

    async def test_a_super_admin_sees_every_organisation(self, client, db):
        boss = await _super_admin(db, 'boss@acmecorp.io')
        await create_org(db, owner_user_id=str(boss.user_id), slug='acme', domains=('acmecorp.io',))

        response = await client.get(
            '/portal/deployment/organizations', headers=auth_header(make_token(str(boss.user_id)))
        )

        assert response.status_code == 200, response.text
        assert [(o['slug'], o['domains'], o['members']) for o in response.json()] == [('acme', ['acmecorp.io'], 1)]


class TestSuperAdmins:
    async def test_one_makes_another_on_a_claimed_domain(self, client, db):
        boss = await _super_admin(db, 'boss@acmecorp.io')
        await create_org(db, owner_user_id=str(boss.user_id), slug='acme', domains=('acmecorp.io',))
        await create_user(db, email='deputy@acmecorp.io')

        response = await client.post(
            '/portal/super-admins',
            json={'email': 'deputy@acmecorp.io'},
            headers=auth_header(make_token(str(boss.user_id))),
        )

        assert response.status_code == 200, response.text

    async def test_not_somebody_off_every_domain(self, client, db):
        boss = await _super_admin(db, 'boss@acmecorp.io')
        await create_user(db, email='stray@elsewhere.io')

        response = await client.post(
            '/portal/super-admins',
            json={'email': 'stray@elsewhere.io'},
            headers=auth_header(make_token(str(boss.user_id))),
        )

        assert response.status_code == 422

    async def test_not_somebody_who_has_not_signed_up(self, client, db):
        boss = await _super_admin(db, 'boss@acmecorp.io')

        response = await client.post(
            '/portal/super-admins',
            json={'email': 'ghost@acmecorp.io'},
            headers=auth_header(make_token(str(boss.user_id))),
        )

        assert response.status_code == 404

    async def test_nobody_else_may(self, client, db):
        user = await create_user(db, email='someone@acmecorp.io')

        response = await client.get('/portal/super-admins', headers=auth_header(make_token(str(user.user_id))))

        assert response.status_code == 403

    async def test_the_last_one_cannot_be_removed(self, client, db):
        boss = await _super_admin(db, 'boss@acmecorp.io')

        response = await client.delete(
            f'/portal/super-admins/{boss.user_id}', headers=auth_header(make_token(str(boss.user_id)))
        )

        assert response.status_code == 409

    async def test_two_removals_at_once_leave_one(self, db):
        first = await _super_admin(db, 'one@acmecorp.io')
        second = await _super_admin(db, 'two@acmecorp.io')

        outcomes = await asyncio.gather(
            UserDB().set_super_admin(str(first.user_id), super_admin=False),
            UserDB().set_super_admin(str(second.user_id), super_admin=False),
            return_exceptions=True,
        )

        assert sum(isinstance(o, LastSuperAdmin) for o in outcomes) == 1
        left = await db.execute(select(func.count()).select_from(User).where(User.is_super_admin.is_(True)))
        assert left.scalar_one() == 1

    async def test_the_last_one_cannot_delete_their_account(self, client, db):
        boss = await _super_admin(db, 'boss@acmecorp.io')

        response = await client.delete('/portal/me', headers=auth_header(make_token(str(boss.user_id))))

        assert response.status_code == 409


class TestTheBootstrapAccountsWayOut:
    async def _bootstrapped(self, client, db) -> tuple[str, str]:
        """The first account, then the organisation it creates on its company's domain."""
        founder = await _signed_up(client, 'installer@vendor-it.io')
        created = await client.post(
            '/portal/organizations',
            json={'display_name': 'Acme', 'domains': ['acmecorp.io']},
            headers=auth_header(make_token(founder)),
        )
        assert created.status_code == 201, created.text
        assert await _memberships(db, founder) == []
        return founder, created.json()['id']

    async def test_it_moves_onto_a_company_address_and_joins(self, client, db, codes_accepted):
        founder, org_id = await self._bootstrapped(client, db)
        headers = auth_header(make_token(founder))

        sent = await client.post('/portal/me/company-address/code', json={'email': 'it@acmecorp.io'}, headers=headers)
        moved = await client.post(
            '/portal/me/company-address', json={'email': 'it@acmecorp.io', 'code': '123456'}, headers=headers
        )

        assert sent.status_code == 200, sent.text
        assert moved.status_code == 200, moved.text
        assert await UserDB().get_by_email('installer@vendor-it.io') is None
        assert (await UserDB().get_by_user_id(founder)).email == 'it@acmecorp.io'
        assert await _memberships(db, founder) == [(org_id, 'admin')]

    async def test_a_wrong_code_moves_nothing(self, client, db, codes_accepted):
        founder, _ = await self._bootstrapped(client, db)

        response = await client.post(
            '/portal/me/company-address',
            json={'email': 'it@acmecorp.io', 'code': '000000'},
            headers=auth_header(make_token(founder)),
        )

        assert response.status_code == 400
        assert (await UserDB().get_by_user_id(founder)).email == 'installer@vendor-it.io'

    async def test_only_onto_a_claimed_domain(self, client, db, codes_accepted):
        founder, _ = await self._bootstrapped(client, db)

        response = await client.post(
            '/portal/me/company-address/code',
            json={'email': 'it@elsewhere.io'},
            headers=auth_header(make_token(founder)),
        )

        assert response.status_code == 403

    async def test_somebody_in_an_organisation_cannot_use_it(self, client, db, codes_accepted):
        owner = await create_user(db, email='owner@acmecorp.io')
        await create_org(db, owner_user_id=str(owner.user_id), slug='acme', domains=('acmecorp.io', 'acme-uk.io'))

        response = await client.post(
            '/portal/me/company-address/code',
            json={'email': 'owner@acme-uk.io'},
            headers=auth_header(make_token(str(owner.user_id))),
        )

        assert response.status_code == 409

    async def test_or_it_makes_a_super_admin_and_leaves(self, client, db, codes_accepted):
        founder, _ = await self._bootstrapped(client, db)
        colleague = await _signed_up(client, 'cto@acmecorp.io')
        headers = auth_header(make_token(founder))

        made = await client.post('/portal/super-admins', json={'email': 'cto@acmecorp.io'}, headers=headers)
        gone = await client.delete('/portal/me', headers=headers)

        assert made.status_code == 200, made.text
        assert gone.status_code in (200, 204), gone.text
        assert await UserDB().get_by_user_id(founder) is None
        assert (await UserDB().get_by_user_id(colleague)).is_super_admin is True


class TestAStaleOrganisationHeader:
    async def test_the_account_routes_still_answer(self, client, db):
        user = await create_user(db, email='left@acmecorp.io')

        response = await client.get(
            '/portal/me', headers={**auth_header(make_token(str(user.user_id))), 'X-Org-Id': 'gone'}
        )

        assert response.status_code == 200, response.text

    async def test_an_org_scoped_route_refuses_rather_than_pick_another(self, client, db):
        user = await create_user(db, email='member@acmecorp.io')
        other = await create_user(db, email='owner@acmecorp.io')
        org = await create_org(db, owner_user_id=str(other.user_id), slug='acme', domains=('acmecorp.io',))
        await add_member(db, str(org.id), str(user.user_id))

        response = await client.get(
            '/portal/team', headers={**auth_header(make_token(str(user.user_id))), 'X-Org-Id': 'not-theirs'}
        )

        assert response.status_code == 403


class TestTheDirectoryFollowsTheDomain:
    TOKEN = 'scim-test-token-that-is-long-enough'

    async def _provision(self, client, address: str):
        return await client.post(
            'http://test/scim/v2/Users',
            json={
                'schemas': ['urn:ietf:params:scim:schemas:core:2.0:User'],
                'userName': address,
                'name': {'givenName': 'Dir', 'familyName': 'Person'},
                'emails': [{'value': address, 'primary': True}],
                'active': True,
            },
            headers={'Authorization': f'Bearer {self.TOKEN}'},
        )

    async def test_a_provisioned_person_joins_their_domains_organisation(self, client, db, monkeypatch):
        owner = await create_user(db, email='owner@acmecorp.io')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug='acme', domains=('acmecorp.io',))
        monkeypatch.setattr(settings, 'scim_token', self.TOKEN)

        response = await self._provision(client, 'dir@acmecorp.io')

        assert response.status_code == 201, response.text
        user = await UserDB().get_by_email('dir@acmecorp.io')
        assert await _memberships(db, str(user.user_id)) == [(str(org.id), 'member')]

    async def test_an_address_off_every_domain_creates_nobody(self, client, db, monkeypatch):
        owner = await create_user(db, email='owner@acmecorp.io')
        await create_org(db, owner_user_id=str(owner.user_id), slug='acme', domains=('acmecorp.io',))
        monkeypatch.setattr(settings, 'scim_token', self.TOKEN)

        response = await self._provision(client, 'dir@elsewhere.io')

        assert response.status_code == 409
        # SCIM's own error shape, inside FastAPI's `detail`.
        assert 'elsewhere.io' in response.json()['detail']['detail']
        assert await UserDB().get_by_email('dir@elsewhere.io') is None
