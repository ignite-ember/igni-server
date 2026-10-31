"""The startup checks, shown to the people who run the deployment (F35).

A startup warning reaches whoever reads the log. For
``LOG_SIGN_IN_CODES`` that is the wrong audience twice over: the log is
where the codes are, and the people who can turn the setting off are the
portal's super admins, who may never read it. So the same warnings are
served to them at ``GET /portal/deployment/notices`` and the portal shows
them as a banner (`portal/src/components/layout/DeploymentBanner.tsx`).
"""

from __future__ import annotations

import pytest

from app.config import settings
from tests.conftest import auth_header, create_user, make_token


@pytest.fixture
def quiet(monkeypatch) -> pytest.MonkeyPatch:
    """A dev deployment the startup checks have nothing to say about."""
    for key, value in {
        'app_env': 'dev',
        'email_enabled': True,
        'email_backend': 'smtp',
        'smtp_host': 'smtp.example.com',
        'log_sign_in_codes': False,
        'node_registration_token': '',
        'secret_encryption_keys_previous': '',
        'super_admin_password_hash': '',
        'oidc_issuer': '',
        'oidc_client_id': '',
        'oidc_client_secret': '',
    }.items():
        monkeypatch.setattr(settings, key, value)
    return monkeypatch


async def _person(db, *, super_admin: bool) -> dict[str, str]:
    email = 'owner@example.com' if super_admin else 'member@example.com'
    user = await create_user(db, email=email, is_super_admin=super_admin)
    return auth_header(make_token(str(user.user_id)))


async def test_nothing_to_say_is_an_empty_list(client, db, quiet) -> None:
    admin = await _person(db, super_admin=True)

    response = await client.get('/portal/deployment/notices', headers=admin)

    assert response.status_code == 200, response.text
    assert response.json() == {'notices': []}


async def test_codes_in_the_log_are_shown_to_a_super_admin(client, db, quiet) -> None:
    quiet.setattr(settings, 'email_enabled', False)
    quiet.setattr(settings, 'smtp_host', '')
    quiet.setattr(settings, 'log_sign_in_codes', True)
    admin = await _person(db, super_admin=True)

    notices = (await client.get('/portal/deployment/notices', headers=admin)).json()['notices']

    (notice,) = notices
    # The startup check's own words, consequence included.
    assert 'LOG_SIGN_IN_CODES is on' in notice
    assert 'sign in as any user' in notice


async def test_a_relay_configured_takes_the_notice_away(client, db, quiet) -> None:
    """Being able to send email is what makes the setting inert."""
    quiet.setattr(settings, 'log_sign_in_codes', True)
    quiet.setattr(settings, 'email_backend', 'ses')
    quiet.setattr(settings, 'smtp_host', '')
    admin = await _person(db, super_admin=True)

    notices = (await client.get('/portal/deployment/notices', headers=admin)).json()['notices']

    # Left on beside a relay is still worth unsetting, and says so —
    # but no longer says codes are in the log.
    (notice,) = notices
    assert 'Unset it' in notice
    assert 'credential material' not in notice


async def test_anybody_else_is_refused(client, db, quiet) -> None:
    quiet.setattr(settings, 'log_sign_in_codes', True)
    quiet.setattr(settings, 'email_enabled', False)
    member = await _person(db, super_admin=False)

    response = await client.get('/portal/deployment/notices', headers=member)

    assert response.status_code == 403
    assert 'LOG_SIGN_IN_CODES' not in response.text


async def test_signed_out_is_refused(client, quiet) -> None:
    assert (await client.get('/portal/deployment/notices')).status_code == 401
