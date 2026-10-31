"""Tests for sign-in flows: password-based and OTP code-based."""

from fastapi import HTTPException, status

from app.services.auth.jwt_service import TokenResponse
from app.services.auth.otp_service import OTPResult, OTPService
from tests.conftest import create_user_with_password


class TestSignIn:
    async def test_sign_in_with_valid_credentials(self, client, db):
        await create_user_with_password(db, password='correct-password', email='alice@test.com', full_name='Alice')

        resp = await client.post(
            '/sign-in',
            json={'email': 'alice@test.com', 'password': 'correct-password'},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert 'access_token' in data
        assert data['token_type'] == 'bearer'

    async def test_sign_in_wrong_password(self, client, db):
        await create_user_with_password(db, password='correct-password', email='alice@test.com')

        resp = await client.post(
            '/sign-in',
            json={'email': 'alice@test.com', 'password': 'wrong-password'},
        )

        assert resp.status_code == 401

    async def test_sign_in_nonexistent_user(self, client):
        resp = await client.post(
            '/sign-in',
            json={'email': 'nobody@test.com', 'password': 'any-password'},
        )

        assert resp.status_code == 401


class TestSignInWithCode:
    async def test_send_signin_code_success(self, client, monkeypatch, email_relay):
        async def fake_generate(email):
            return OTPResult(message=f'Code sent to {email}', code='123456', email=email)

        monkeypatch.setattr(OTPService, 'generate_signin_code', staticmethod(fake_generate))

        resp = await client.post('/send-sign-in-code', json={'email': 'user@test.com'})

        assert resp.status_code == 200
        assert 'Code sent to' in resp.json()['message']

    async def test_signin_with_valid_code(self, client, monkeypatch):
        async def fake_validate(email, code):
            return TokenResponse(access_token='fake-jwt-token')

        monkeypatch.setattr(
            OTPService,
            'validate_and_signin_with_code',
            staticmethod(fake_validate),
        )

        resp = await client.post(
            '/sign-in-with-code',
            json={'email': 'user@test.com', 'code': '123456'},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data['access_token'] == 'fake-jwt-token'

    async def test_signin_with_invalid_code(self, client, monkeypatch):
        async def fake_validate(email, code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Invalid or expired verification code',
            )

        monkeypatch.setattr(
            OTPService,
            'validate_and_signin_with_code',
            staticmethod(fake_validate),
        )

        resp = await client.post(
            '/sign-in-with-code',
            json={'email': 'user@test.com', 'code': '000000'},
        )

        assert resp.status_code == 401
