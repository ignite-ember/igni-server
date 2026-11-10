"""Tests for password reset flows."""

from app.services.auth.otp_service import OTPResult, OTPService
from tests.conftest import create_user_with_password


class TestPasswordReset:
    async def test_send_reset_code(self, client, monkeypatch, email_relay):
        async def fake_generate(email):
            return OTPResult(
                message=f'Password reset code has been sent to {email}',
                code='654321',
                email=email,
            )

        monkeypatch.setattr(
            OTPService,
            'generate_password_reset_code',
            staticmethod(fake_generate),
        )

        resp = await client.post('/send-reset-password-code', json={'email': 'user@test.com'})

        assert resp.status_code == 200
        assert 'reset code' in resp.json()['message'].lower()

    async def test_reset_password_success(self, client, db, monkeypatch):
        await create_user_with_password(db, password='old-password', email='reset@test.com')

        # Monkeypatch the OTP reset to skip Redis but still update the password
        # We patch the full method to validate=noop, then update password via UserDB
        async def fake_reset(email, code, new_password):
            import bcrypt

            from app.db.sql.user import UserDB

            _user_db = UserDB()
            user = await _user_db.get_by_email(email)
            if not user:
                from fastapi import HTTPException, status

                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
            hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
            await _user_db.update_password(user_id=str(user.user_id), hashed_password=hashed)
            return 'Password has been reset successfully. Please sign in with your new password.'

        monkeypatch.setattr(OTPService, 'reset_password_with_code', staticmethod(fake_reset))

        resp = await client.post(
            '/reset-password',
            json={
                'email': 'reset@test.com',
                'code': '654321',
                'new_password': 'NewPass@123',
            },
        )

        assert resp.status_code == 200
        assert 'reset successfully' in resp.json()['message'].lower()

    async def test_reset_then_signin_with_new_password(self, client, db, monkeypatch):
        await create_user_with_password(db, password='old-password', email='flow@test.com')

        # Monkeypatch reset to actually update the password (skip Redis only)
        async def fake_reset(email, code, new_password):
            import bcrypt

            from app.db.sql.user import UserDB

            _user_db = UserDB()
            user = await _user_db.get_by_email(email)
            hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
            await _user_db.update_password(user_id=str(user.user_id), hashed_password=hashed)
            return 'Password has been reset successfully.'

        monkeypatch.setattr(OTPService, 'reset_password_with_code', staticmethod(fake_reset))

        # Reset the password
        resp = await client.post(
            '/reset-password',
            json={
                'email': 'flow@test.com',
                'code': '654321',
                'new_password': 'Brand@new1',
            },
        )
        assert resp.status_code == 200

        # Sign in with the new password should succeed
        resp = await client.post(
            '/sign-in',
            json={'email': 'flow@test.com', 'password': 'Brand@new1'},
        )
        assert resp.status_code == 200
        assert 'access_token' in resp.json()

        # Sign in with the old password should fail
        resp = await client.post(
            '/sign-in',
            json={'email': 'flow@test.com', 'password': 'old-password'},
        )
        assert resp.status_code == 401


class TestTheRealReset:
    """Through the real `OTPService`, with a real code: nothing stubbed but the race."""

    async def test_a_right_code_sets_the_new_password(self, client, db):
        import uuid

        email = f'real-reset-{uuid.uuid4().hex[:8]}@test.com'
        await create_user_with_password(db, password='old-password', email=email)
        await db.commit()
        issued = await OTPService().generate_password_reset_code(email)

        resp = await client.post(
            '/reset-password', json={'email': email, 'code': issued.code, 'new_password': 'Brand@new1'}
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            'message': 'Password has been reset successfully. Please sign in with your new password.'
        }
        signed_in = await client.post('/sign-in', json={'email': email, 'password': 'Brand@new1'})
        assert signed_in.status_code == 200, signed_in.text

    async def test_an_account_gone_before_the_update_is_a_404(self, client, db, monkeypatch):
        # Found by address, then deleted before the new password is
        # written: the store's refusal is the same 404 a missing account
        # has always had, not a 500.
        import uuid

        from app.db.sql.user import UserDB

        class VanishingUsers(UserDB):
            async def get_by_email(self, email):
                user = await super().get_by_email(email)
                if user is not None:
                    await UserDB().delete(str(user.user_id))
                return user

        email = f'vanishing-{uuid.uuid4().hex[:8]}@test.com'
        await create_user_with_password(db, password='old-password', email=email)
        await db.commit()
        issued = await OTPService().generate_password_reset_code(email)
        monkeypatch.setattr(OTPService(), '_users', VanishingUsers())

        resp = await client.post(
            '/reset-password', json={'email': email, 'code': issued.code, 'new_password': 'Brand@new1'}
        )

        assert resp.status_code == 404, resp.text
        assert resp.json() == {'detail': 'User not found'}
