"""The break-glass key: what lets a caller change the deployment.

`app.security.deployment_admin.require_deployment_admin` is the gate on
deployment-wide settings. Its session half was covered by the route tests;
the `X-Super-Admin-Key` half — the one that works when sign-in is broken —
had no test at all, so a change that accepted any key, or accepted a key
when none is configured, would have passed the suite.

Called directly rather than through a route: the decision is the function's,
and the four outcomes are easier to read here than behind four endpoints.
"""

from __future__ import annotations

from types import SimpleNamespace

import bcrypt
from fastapi import HTTPException
import pytest

from app.config import settings
from app.security.deployment_admin import require_deployment_admin

_KEY = 'break-glass-for-tests-only'
_REQUEST = SimpleNamespace(client=SimpleNamespace(host='203.0.113.7'))


@pytest.fixture
def configured_key(monkeypatch):
    monkeypatch.setattr(settings, 'super_admin_password_hash', bcrypt.hashpw(_KEY.encode(), bcrypt.gensalt(4)).decode())


async def test_the_right_key_is_let_in_and_names_nobody(configured_key):
    assert await require_deployment_admin(_REQUEST, x_super_admin_key=_KEY, current_user=None) is None


async def test_a_wrong_key_is_refused(configured_key):
    with pytest.raises(HTTPException) as refused:
        await require_deployment_admin(_REQUEST, x_super_admin_key='not-the-key', current_user=None)
    assert refused.value.status_code == 403


async def test_no_key_is_accepted_when_none_is_configured(monkeypatch):
    """Unset means there is no break-glass — not that every key works."""
    monkeypatch.setattr(settings, 'super_admin_password_hash', '')
    with pytest.raises(HTTPException) as refused:
        await require_deployment_admin(_REQUEST, x_super_admin_key=_KEY, current_user=None)
    assert refused.value.status_code == 501


async def test_a_wrong_key_does_not_fall_through_to_a_signed_in_member(configured_key):
    """A member who is not a super admin, sending a bad key, is refused."""
    member = SimpleNamespace(is_super_admin=False)
    with pytest.raises(HTTPException) as refused:
        await require_deployment_admin(_REQUEST, x_super_admin_key='not-the-key', current_user=member)
    assert refused.value.status_code == 403


async def test_a_super_admins_session_needs_no_key(monkeypatch):
    monkeypatch.setattr(settings, 'super_admin_password_hash', '')
    admin = SimpleNamespace(is_super_admin=True)
    assert await require_deployment_admin(_REQUEST, x_super_admin_key=None, current_user=admin) is admin


async def test_a_member_without_a_key_is_forbidden_and_a_stranger_unauthorised():
    signed_in = SimpleNamespace(is_super_admin=False)
    with pytest.raises(HTTPException) as member:
        await require_deployment_admin(_REQUEST, x_super_admin_key=None, current_user=signed_in)
    with pytest.raises(HTTPException) as stranger:
        await require_deployment_admin(_REQUEST, x_super_admin_key=None, current_user=None)
    assert (member.value.status_code, stranger.value.status_code) == (403, 401)
