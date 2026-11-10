"""A session token cannot be turned into a permanent credential. F110.

`POST /v1/portal/me/set-password` replaced the account password given
nothing but a session token — no current password, and no check whether
one already existed. Demonstrated end to end before removal:

    sign-in with the original password:   200
    set-password with only a token:       200
    sign-in with the ATTACKER's password: 200
    sign-in with the original password:   401

So a leaked token — from browser history, a proxy log, a shared machine,
or the query string every OAuth callback used until F107 — became a
permanent credential that outlives the token and locks the owner out.

It also bypassed the password policy: `SetPasswordRequest` never applied
`_validate_password_strength`, so a one-character password was accepted
here and then worked at sign-in, while sign-up and reset both demand
eight characters with mixed classes.

Removed rather than repaired, because nothing called it and its stated
purpose — letting an OAuth user add a password — is already served by the
reset flow, which requires proving control of the mailbox. The tests here
are the three claims that removal rests on: the route is gone, the
takeover no longer works, and the sanctioned path does.
"""

from __future__ import annotations

import pathlib
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.services.auth.passwords import Passwords
from tests.conftest import auth_header, make_token, TEST_DB_URL_SYNC

_ROOT = pathlib.Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.anyio


def _seed_user_with_password(password: str) -> tuple[str, str, str]:
    """A user with a real bcrypt hash, an org, and a membership.

    Synchronous, for the reason `test_audit_trail.py` documents: the `db`
    fixture and the `client` fixture run on different event loops.
    """
    email = f'victim-{uuid.uuid4().hex[:8]}@example.com'
    user_id, org_id = str(uuid.uuid4()), str(uuid.uuid4())

    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    'INSERT INTO users (id, user_id, email, hashed_password, full_name, auth_provider,'
                    " created_at, updated_at) VALUES (:i, :u, :e, :h, 'Victim', 'email', now(), now())"
                ),
                {
                    'i': str(uuid.uuid4()),
                    'u': user_id,
                    'e': email,
                    'h': Passwords().hash(password),
                },
            )
            conn.execute(
                text(
                    'INSERT INTO organizations (id, slug, display_name, owner_user_id, created_at,'
                    " updated_at) VALUES (:i, :s, 'Victim Org', :o, now(), now())"
                ),
                {'i': org_id, 's': f'o-{uuid.uuid4().hex[:8]}', 'o': user_id},
            )
            conn.execute(
                text(
                    'INSERT INTO org_memberships (id, org_id, user_id, role, status, created_at,'
                    " updated_at) VALUES (:i, :g, :u, 'member', 'active', now(), now())"
                ),
                {'i': str(uuid.uuid4()), 'g': org_id, 'u': user_id},
            )
    finally:
        engine.dispose()

    return user_id, org_id, email


class TestTheEndpointIsGone:
    async def test_it_is_not_routed(self, client):
        """With a token for a real, live user — which matters.

        The first version used a random user id, and passed with the
        endpoint restored: `get_current_portal_user` answers 404 for an
        unknown user, so the assertion could not tell a missing route
        from a missing account. Found by running the revert check and
        noticing this test was not among the ones that failed.
        """
        _user_id, org_id, _email = _seed_user_with_password('The-Original-1!')
        token = make_token(_user_id, org_id=org_id, org_name='Victim Org')

        # The same token reaches a route that does exist, so a 404 below
        # is about the route and not about the caller.
        alive = await client.get('/portal/me', headers=auth_header(token))
        assert alive.status_code == 200, f'the token is not usable: {alive.text}'

        response = await client.post(
            '/portal/me/set-password',
            headers=auth_header(token),
            json={'new_password': 'Whatever-1!'},
        )

        assert response.status_code == 404, (
            f'/portal/me/set-password answered {response.status_code}. It replaced the account '
            'password given only a session token.'
        )

    def test_the_schema_is_gone_too(self):
        """A request model with no endpoint is how an endpoint comes
        back."""
        source = (_ROOT / 'app/api/base_v1/schema/portal/user.py').read_text()

        assert 'class SetPasswordRequest' not in source


class TestTheTakeoverNoLongerWorks:
    async def test_a_token_holder_cannot_change_the_password(self, client):
        """The exploit, run in full. Each step is asserted rather than the
        last one only: if the first sign-in failed for an unrelated
        reason, a passing test would prove nothing."""
        original = 'The-Original-Password-1!'
        user_id, org_id, email = _seed_user_with_password(original)

        before = await client.post('/sign-in', json={'email': email, 'password': original})
        assert before.status_code == 200, f'setup failed: {before.text}'

        stolen = make_token(user_id, org_id=org_id, org_name='Victim Org')
        attacker = 'The-Attackers-Password-1!'

        attempt = await client.post(
            '/portal/me/set-password',
            headers=auth_header(stolen),
            json={'new_password': attacker},
        )
        assert attempt.status_code == 404, attempt.status_code

        # The owner still gets in, and the attacker does not.
        assert (await client.post('/sign-in', json={'email': email, 'password': original})).status_code == 200
        assert (await client.post('/sign-in', json={'email': email, 'password': attacker})).status_code == 401


class TestTheSanctionedPathStillWorks:
    """Removal is only defensible if the replacement does the job. It has
    to work for an account with *no* password, which is the case the
    removed endpoint existed for."""

    async def test_a_reset_code_sets_a_password_on_an_oauth_account(self, client, monkeypatch):
        from app.services.auth.otp_service import OTPService

        email = f'oauth-{uuid.uuid4().hex[:8]}@example.com'
        engine = create_engine(TEST_DB_URL_SYNC)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        'INSERT INTO users (id, user_id, email, hashed_password, full_name,'
                        ' auth_provider, created_at, updated_at)'
                        " VALUES (:i, :u, :e, '', 'OAuth Only', 'github', now(), now())"
                    ),
                    {'i': str(uuid.uuid4()), 'u': str(uuid.uuid4()), 'e': email},
                )
        finally:
            engine.dispose()

        # An empty hash: no password at all. Sign-in must refuse rather
        # than raise — the F44 malformed-hash guard.
        first = await client.post('/sign-in', json={'email': email, 'password': 'anything'})
        assert first.status_code == 401, first.status_code

        # The code goes to the mailbox in a real deployment. Here it is
        # taken from the generator, which is the same value.
        issued = await OTPService().generate_password_reset_code(email=email)

        chosen = 'A-Password-They-Chose-1!'
        reset = await client.post('/reset-password', json={'email': email, 'code': issued.code, 'new_password': chosen})
        assert reset.status_code == 200, reset.text

        after = await client.post('/sign-in', json={'email': email, 'password': chosen})
        assert after.status_code == 200, (
            'the reset flow does not work for an account with no password, so removing '
            'set-password left OAuth users unable to add one'
        )

    async def test_the_reset_flow_enforces_the_password_policy(self, client):
        """The other half of what the removed endpoint got wrong."""
        from app.services.auth.otp_service import OTPService

        email = f'weak-{uuid.uuid4().hex[:8]}@example.com'
        issued = await OTPService().generate_password_reset_code(email=email)

        response = await client.post('/reset-password', json={'email': email, 'code': issued.code, 'new_password': 'a'})

        assert response.status_code == 422, response.status_code


class TestEveryPasswordFieldAppliesThePolicy:
    """The rule, rather than the one endpoint. `SetPasswordRequest` was
    the only password-setting model that skipped
    `_validate_password_strength`, and nothing would have reported a
    second one."""

    def test_every_new_password_field_validates_strength(self):
        import inspect

        from app.api.base_v1.schema import auth as auth_schema

        offenders = []
        modules = [auth_schema]

        for module_path in ('app/api/base_v1/schema/portal/user.py',):
            source = (_ROOT / module_path).read_text()
            if 'new_password' in source and '_validate_password_strength' not in source:
                offenders.append(module_path)

        for module in modules:
            for _name, obj in inspect.getmembers(module, inspect.isclass):
                fields = getattr(obj, 'model_fields', None)
                if not fields or 'new_password' not in fields:
                    continue
                validators = getattr(obj, '__pydantic_decorators__', None)
                names = set(getattr(validators, 'field_validators', {}) or {})
                if not names:
                    offenders.append(f'{module.__name__}.{obj.__name__}')

        assert not offenders, (
            f'{offenders} accept a new password without applying the strength policy that '
            'sign-up and reset both enforce.'
        )

    def test_the_policy_itself_is_not_vacuous(self):
        """A validator that accepts everything would make the rule above
        pass while enforcing nothing."""
        from app.api.base_v1.schema.auth import _validate_password_strength

        for weak in ('a', 'short', 'alllowercase1!', 'ALLUPPERCASE1!', 'NoDigits!!', 'NoSymbols1'):
            with pytest.raises(ValueError):
                _validate_password_strength(weak)

        assert _validate_password_strength('Str0ng-Enough!') == 'Str0ng-Enough!'
