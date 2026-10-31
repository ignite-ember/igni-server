"""Tests for sign-up flows including OTP validation and invite claiming."""

from fastapi import HTTPException, status
from sqlalchemy import select

from app.db.sql.models.org_membership import OrgMembership
from app.services.auth.otp_service import OTPResult, OTPService
from tests.conftest import (
    add_invited_member,
    create_org,
    create_user,
)


class TestSendSignUpCode:
    async def test_send_code_new_email(self, client, monkeypatch, email_relay):
        async def fake_generate(email):
            return OTPResult(
                message=f'Verification code has been sent to {email}',
                code='123456',
                email=email,
            )

        monkeypatch.setattr(OTPService, 'generate_signup_code', staticmethod(fake_generate))

        resp = await client.post('/send-sign-up-code', json={'email': 'new@test.com'})

        assert resp.status_code == 200
        assert 'Verification code' in resp.json()['message']

    async def test_send_code_existing_user(self, client, db, monkeypatch):
        """A registered address is answered exactly like an unknown one.

        This asserted `400` — which was the account-enumeration oracle
        F112 closed, not a feature. An unauthenticated caller could test
        any address and learn whether it had an account, unlimited, while
        a comment in the endpoint file promised the response "is
        identical whether or not the address belongs to a real account".

        Rewritten to assert the fix, and asserting *both* responses
        rather than one status code: "returns 200" would pass against a
        200 whose body said "already registered".
        """
        await create_user(db, email='existing@test.com')

        async def fake_generate(email):
            return OTPResult(message='sent', code='123456', email=email)

        monkeypatch.setattr(OTPService, 'generate_signup_code', staticmethod(fake_generate))

        known = await client.post('/send-sign-up-code', json={'email': 'existing@test.com'})
        unknown = await client.post('/send-sign-up-code', json={'email': 'nobody-at-all@test.com'})

        assert known.status_code == unknown.status_code == 200, (known.status_code, unknown.status_code)
        assert known.json() == unknown.json(), (known.text, unknown.text)


class TestSignUp:
    async def test_signup_success(self, client, monkeypatch):
        async def fake_validate(email, code):
            return None  # no-op, validation passes

        monkeypatch.setattr(OTPService, 'validate_signup_code', staticmethod(fake_validate))

        resp = await client.post(
            '/sign-up',
            json={
                'first_name': 'Jane',
                'last_name': 'Doe',
                'email': 'jane@test.com',
                'code': '123456',
                'password': 'Secure@pass1',
            },
        )

        assert resp.status_code == 201
        data = resp.json()
        assert 'access_token' in data
        assert data['token_type'] == 'bearer'

    async def test_signup_creates_no_organisation(self, client, db, monkeypatch):
        """An account starts in none; a person joins one by invitation.
        It used to be given a personal organisation here. The rest of the
        rule is in tests/test_a_person_belongs_to_their_domains_organisation.py."""

        async def fake_validate(email, code):
            return None

        monkeypatch.setattr(OTPService, 'validate_signup_code', staticmethod(fake_validate))

        resp = await client.post(
            '/sign-up',
            json={
                'first_name': 'Org',
                'last_name': 'Test',
                'email': 'orgtest@test.com',
                'code': '123456',
                'password': 'Secure@pass1',
            },
        )
        assert resp.status_code == 201

        result = await db.execute(select(OrgMembership).where(OrgMembership.status == 'active'))
        assert result.scalars().all() == []

    async def test_signup_invalid_code(self, client, monkeypatch):
        async def fake_validate(email, code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Invalid or expired verification code',
            )

        monkeypatch.setattr(OTPService, 'validate_signup_code', staticmethod(fake_validate))

        resp = await client.post(
            '/sign-up',
            json={
                'first_name': 'Jane',
                'last_name': 'Doe',
                'email': 'jane@test.com',
                'code': '000000',
                'password': 'Secure@pass1',
            },
        )

        assert resp.status_code == 401

    async def test_signup_duplicate_email(self, client, db, monkeypatch):
        await create_user(db, email='dup@test.com')

        async def fake_validate(email, code):
            return None

        monkeypatch.setattr(OTPService, 'validate_signup_code', staticmethod(fake_validate))

        resp = await client.post(
            '/sign-up',
            json={
                'first_name': 'Jane',
                'last_name': 'Doe',
                'email': 'dup@test.com',
                'code': '123456',
                'password': 'Secure@pass1',
            },
        )

        # UserDB.create raises 409 for duplicate email
        assert resp.status_code == 409

    async def test_signup_claims_invited_memberships(self, client, db, verify_db, monkeypatch):
        # Setup: create an org with an owner, then add an invited member
        await create_user(db, user_id='owner-1', email='owner@test.com')
        org = await create_org(db, owner_user_id='owner-1', slug='invite-org', domains=('test.com',))
        invited = await add_invited_member(
            db,
            org_id=str(org.id),
            email='newbie@test.com',
            role='member',
            invited_by='owner-1',
        )
        invited_membership_id = str(invited.id)

        # Monkeypatch OTP validation to pass
        async def fake_validate(email, code):
            return None

        monkeypatch.setattr(OTPService, 'validate_signup_code', staticmethod(fake_validate))

        # Sign up with the invited email
        resp = await client.post(
            '/sign-up',
            json={
                'first_name': 'Newbie',
                'last_name': 'User',
                'email': 'newbie@test.com',
                'code': '123456',
                'password': 'Secure@pass1',
            },
        )

        assert resp.status_code == 201

        # Verify via fresh session (app singletons committed in their own sessions)
        result = await verify_db.execute(select(OrgMembership).where(OrgMembership.id == invited_membership_id))
        membership = result.scalars().first()
        assert membership is not None
        assert membership.status == 'active'
        assert not str(membership.user_id).startswith('invited-')
