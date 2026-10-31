"""The dev bypass writes a real admin row, and it outlives the flag.

`PORTAL_AUTH_BYPASS` is refused outside dev, so the bypass cannot
*operate* in production — F52's work. What that guard does not cover is
that the bypass does not only skip authentication: it calls
`ensure_exists` for a user and an organisation, so any database a
bypass-enabled server has ever touched keeps

    users:         demo-user-001, hashed_password ''
    organizations: demo-org-001, owned by that user, role admin

A developer running locally against a database that is later promoted,
restored into production, or simply reused, ships a live administrator
account.

Proven end to end against a server with `APP_ENV=production` and
`PORTAL_AUTH_BYPASS=false`:

    POST /v1/send-sign-in-code {"email": "alex@igni.sh"}  -> 200
    POST /v1/sign-in-with-code {...}                      -> access_token
    GET  /v1/portal/me    -> {"role": "admin", "org_id": "demo-org-001"}
    GET  /v1/portal/team  -> 200

The one-time code is the only obstacle, and with `EMAIL_ENABLED=false` —
the default, and the documented air-gapped configuration — codes go to the
server log for an operator to relay, so log access is enough.

Two layers, both tested here: the identity's email now lives at an
RFC 2606 `.invalid` domain so no mailbox can ever receive its code, and
the server refuses to start against a database that carries the rows.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import text

from app.config import settings
from app.security.startup_checks import (
    check_no_bypass_identity_in_database,
    UnsafeDeployment,
)

pytestmark = pytest.mark.anyio

_BYPASS_USER_ID = 'demo-user-001'
_BYPASS_ORG_ID = 'demo-org-001'


@pytest.fixture
async def bypass_rows(db):
    """Plant exactly what a bypass-enabled server leaves behind."""
    now = datetime.datetime.now(datetime.timezone.utc)
    await db.execute(
        text(
            'INSERT INTO users (id, user_id, email, hashed_password, auth_provider, '
            'created_at, updated_at) VALUES (:i, :u, :e, :h, :a, :n, :n)'
        ),
        {
            'i': str(uuid.uuid4()),
            'u': _BYPASS_USER_ID,
            'e': 'alex@demo.invalid',
            'h': '',
            'a': 'email',
            'n': now,
        },
    )
    await db.execute(
        text(
            'INSERT INTO organizations (id, slug, display_name, owner_user_id, '
            'created_at, updated_at) VALUES (:i, :s, :d, :o, :n, :n)'
        ),
        {
            'i': _BYPASS_ORG_ID,
            's': 'demo',
            'd': 'Demo Org',
            'o': _BYPASS_USER_ID,
            'n': now,
        },
    )
    await db.commit()
    yield


class TestItRefusesToServeSuchADatabase:
    @pytest.mark.parametrize('app_env', ['production', 'prod', 'staging'])
    async def test_startup_fails_outside_dev(self, monkeypatch, bypass_rows, app_env):
        monkeypatch.setattr(settings, 'app_env', app_env)

        with pytest.raises(UnsafeDeployment) as raised:
            await check_no_bypass_identity_in_database()

        message = str(raised.value)
        assert _BYPASS_USER_ID in message
        assert _BYPASS_ORG_ID in message
        # An operator needs to know what to do, not only that something is
        # wrong — the fix is two DELETEs and the message carries them.
        assert 'DELETE FROM users' in message, message
        assert 'DELETE FROM organizations' in message, message

    async def test_only_the_user_row_is_enough(self, monkeypatch, db):
        """The org can be deleted on its own — the user is the account."""
        now = datetime.datetime.now(datetime.timezone.utc)
        await db.execute(
            text(
                'INSERT INTO users (id, user_id, email, hashed_password, auth_provider, '
                'created_at, updated_at) VALUES (:i, :u, :e, :h, :a, :n, :n)'
            ),
            {
                'i': str(uuid.uuid4()),
                'u': _BYPASS_USER_ID,
                'e': 'alex@demo.invalid',
                'h': '',
                'a': 'email',
                'n': now,
            },
        )
        await db.commit()
        monkeypatch.setattr(settings, 'app_env', 'production')

        with pytest.raises(UnsafeDeployment):
            await check_no_bypass_identity_in_database()

    async def test_a_clean_database_starts(self, monkeypatch, db):
        monkeypatch.setattr(settings, 'app_env', 'production')

        assert await check_no_bypass_identity_in_database() == []

    async def test_dev_is_left_alone(self, monkeypatch, bypass_rows):
        """The rows are the point in dev — refusing there would break the
        bypass this exists to clean up after."""
        monkeypatch.setattr(settings, 'app_env', 'dev')

        assert await check_no_bypass_identity_in_database() == []


class TestAnUnreachableDatabaseIsNotAVerdict:
    async def test_it_reports_nothing_rather_than_guessing(self, monkeypatch):
        """`/ready` is what answers "is the database up". Failing startup on
        a connection blip would turn it into an outage, and passing silently
        would be worse — so this returns nothing and says in the log that it
        skipped rather than passed."""
        monkeypatch.setattr(settings, 'app_env', 'production')
        monkeypatch.setattr(settings, 'postgres_host', '127.0.0.1')
        monkeypatch.setattr(settings, 'postgres_port', 1)

        assert await check_no_bypass_identity_in_database() == []


class TestTheIdentityItselfCannotReceiveMail:
    def test_the_bypass_email_is_at_an_unroutable_domain(self):
        """RFC 2606 reserves `.invalid` precisely so it can never resolve.
        It used to be `alex@igni.sh` — a routable address at a domain the
        customer does not control, and a one-time code sent there is an
        admin session."""
        from app.security.bypass_identity import BYPASS_EMAIL

        assert BYPASS_EMAIL.endswith('.invalid'), BYPASS_EMAIL

    def test_the_fallback_address_is_too(self):
        """`ensure_exists` is called with `email or <fallback>`; the fallback
        was `demo@igni.sh` and had the same problem."""
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parent.parent
        # Both files. The address moved to `bypass_identity` when the
        # ids were de-duplicated, and a rule that kept reading only
        # `portal_auth` would have gone on passing while checking a
        # file the constant had left.
        watched = ['app/security/portal_auth.py', 'app/security/bypass_identity.py']
        for name in watched:
            source = (root / name).read_text()
            # Comments are stripped first: these modules explain what the
            # old address was and why it changed, and that prose must not
            # trip the rule. The first version of this test did exactly
            # that.
            code = re.sub(r'#[^\n]*', '', source)

            assert 'igni.sh' not in code, (
                f'{name} still names a routable address in code; a code delivered '
                'there is an admin session on any deployment carrying these rows'
            )


class TestTheCheckIsActuallyWired:
    def test_the_lifespan_awaits_it(self):
        """Asserted on the source because the behavioural tests above call
        the function directly. A check nobody calls is the failure mode this
        review has recorded twice — `bandit` before F42, `@axe-core` in
        F68 — and here it would leave a live admin account in place."""
        import pathlib

        main = (pathlib.Path(__file__).resolve().parent.parent / 'app/main.py').read_text()

        assert 'check_no_bypass_identity_in_database' in main, 'the check is never called'
        assert 'await check_no_bypass_identity_in_database()' in main, (
            'the check is imported but not awaited — a coroutine that is never '
            'awaited returns a coroutine object, which is truthy and raises nothing'
        )


class TestTheGuardAndTheIdentityCannotDrift:
    """The refuse-to-start check keys on the ids that create the rows.

    `portal_auth` wrote `demo-user-001` / `demo-org-001` and
    `startup_checks` looked for them — from its own separate copies of
    both strings. Change one and the guard goes on passing while the
    admin account it exists to catch is still in the database, which
    is the one failure a refuse-to-start check cannot afford: nothing
    else reports it.
    """

    def test_both_sides_read_the_same_constants(self):
        from app.security import portal_auth, startup_checks
        from app.security.bypass_identity import BYPASS_ORG_ID, BYPASS_USER_ID

        assert startup_checks.BYPASS_USER_ID is BYPASS_USER_ID
        assert startup_checks.BYPASS_ORG_ID is BYPASS_ORG_ID
        assert portal_auth.BYPASS_ORG_ID is BYPASS_ORG_ID
        assert portal_auth._bypass_user().user_id is BYPASS_USER_ID

    def test_neither_module_hardcodes_the_ids_again(self):
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parent.parent
        for name in ('app/security/portal_auth.py', 'app/security/startup_checks.py'):
            code = re.sub(r'#[^\n]*', '', (root / name).read_text())
            # The literals belong in `bypass_identity` and nowhere else.
            # `startup_checks` prints them in an operator-facing message,
            # which is why this reads the code and not the strings it
            # formats — those interpolate the constants.
            assert "'demo-user-001'" not in code, name
            assert "'demo-org-001'" not in code, name


class TestTheBypassIdentityIsNotSharedMutableState:
    def test_each_call_gets_its_own(self):
        """It was a module-level `PortalUser`, and pydantic models are
        mutable by default — so a privileged identity sat where any
        importer could rewrite its `org_role` for the life of the
        process. Nothing did. Nothing had to be able to."""
        from app.security.portal_auth import _bypass_user

        first = _bypass_user()
        first.org_role = 'member'

        assert _bypass_user().org_role == 'admin'
