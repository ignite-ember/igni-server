"""Removing somebody's account has to end their session.

F122. Tokens are signed and carry a 30-day expiry, and nothing consulted
the `users` table when validating one. Deleting an account therefore did
not revoke it — the only revocation an operator has is removing what the
token refers to, and that did not work either.

Demonstrated on the live path:

    user gone-05091c deleted; token still in hand
      OrgContexts.resolve     -> ('gone-05091c', None)
      POST /chat/completions  -> 404 "Model 'x' is not served"

A 404 about models means authorisation passed and the request reached
routing. With a model name this deployment serves it would have been
answered — a deleted developer spending the customer's GPU.

The portal side did more than allow it. With no `X-Org-Id` header,
`get_or_create_personal_org` **created** an organisation for the deleted
user id and returned them as its `admin`, so the first request after
offboarding provisioned fresh state for somebody who no longer existed.

## Why this is a sweep and not two tests

Two call sites turned a token into a user id and both were wrong, which
is the argument for not fixing two call sites: the third one appears
later and nobody notices. So the check here is generated from the **live
router** — every authenticated route, with a token for a user who has
been deleted, must refuse. A new route is covered the day it is added.

That is the F114 lesson stated as a test: an audit policy named three
prefixes while the routes that mattered sat under a fourth, and the
policy was not wrong so much as aimed.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.services.auth.jwt_service import JWTService
from tests.conftest import TEST_DB_URL_SYNC

pytestmark = pytest.mark.anyio

_ABSOLUTE = 'http://test'

#: Prefixes whose authentication is not a user token, so a user's
#: existence is not what gates them. Each is authorised by a shared
#: secret and covered elsewhere.
_NOT_USER_AUTHENTICATED = (
    '/v1/nodes',  # NODE_REGISTRATION_TOKEN, for a GPU cluster
    '/v1/admin/',  # X-Super-Admin-Key
    # A registered service's own token, for one organisation — not a
    # person's. Covered by test_a_service_sees_one_organisation.py.
    '/v1/directory/',
    '/v1/service-notifications',
    # SCIM_TOKEN, presented by the identity provider. Not a person's
    # credential at all: provisioning runs on a schedule with nobody
    # signed in, and a token tied to an employee would stop working
    # the day that employee left — the event it exists to handle.
    '/scim/v2/',
)

#: Routes that are meant to work without a token at all: signing in,
#: signing up, the OAuth and OIDC dances, the health endpoints. A deleted
#: user reaching these is not a finding — it is the product.
#:
#: Enumerated rather than matched by prefix. The first version of this
#: list excluded `auth/` and `cli/` wholesale, which quietly dropped
#: `POST /v1/auth/github/link/start`, its GitLab twin and
#: `POST /v1/cli/token` — all three authenticated, and the last one mints
#: a *new* 30-day token. My own exclusion pattern was aimed rather than
#: reasoned, which is the F114 mistake committed inside the test written
#: to catch it.
_PUBLIC = {
    '/v1/sign-in',
    '/v1/sign-up',
    '/v1/sign-in-with-code',
    '/v1/send-sign-up-code',
    '/v1/send-sign-in-code',
    '/v1/send-reset-password-code',
    '/v1/reset-password',
    '/v1/auth/exchange',
    '/v1/auth/methods',
    '/v1/auth/oidc',
    '/v1/auth/oidc/callback',
    '/v1/auth/{provider}',
    '/v1/auth/{provider}/callback',
    '/health',
    '/ready',
    '/version',
    '/metrics',
    '/docs',
    '/redoc',
    '/openapi.json',
    '/docs/oauth2-redirect',
    # A service or app registering itself, and the token endpoint, which
    # authenticates the client by its own signed assertion. The one
    # grant that takes a person's session — the CLI's token exchange —
    # refuses a deleted, deactivated or withdrawn one; that is tested in
    # test_a_service_token_says_who_and_where.py rather than here,
    # because the session travels in the form, not in a header.
    '/v1/clients',
    # A service uploading its own extension, authenticated by the
    # signature over the bundle rather than by anybody's token.
    '/v1/clients/{client_id}/extension',
    # And each asset it declares, authenticated the same way: by the
    # signature over the asset's digest and size.
    '/v1/clients/{client_id}/extension/assets/{sha256}',
    '/v1/oauth/token',
    '/.well-known/openid-configuration',
    '/.well-known/jwks.json',
}


def _user_authenticated_routes() -> list[tuple[str, str]]:
    """Every route a developer's own token is supposed to open."""
    from app.main import app

    routes: set[tuple[str, str]] = set()
    for path, operations in app.openapi()['paths'].items():
        if path.startswith(_NOT_USER_AUTHENTICATED) or path in _PUBLIC:
            continue
        for method in operations:
            if method.upper() in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
                routes.add((method.upper(), path))
    return sorted(routes)


def _create_then_delete_a_user() -> str:
    """A token for somebody who existed and then did not.

    Synchronous, for the reason `test_audit_trail.py` documents: the
    `client` fixture runs on its own event loop.
    """
    user_id = f'deleted-{uuid.uuid4().hex[:8]}'
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    'INSERT INTO users (id, user_id, email, hashed_password, full_name, created_at, updated_at) '
                    "VALUES (:i, :u, :e, 'x', 'Departed Person', now(), now())"
                ),
                {'i': str(uuid.uuid4()), 'u': user_id, 'e': f'{user_id}@example.com'},
            )
        token = JWTService().create_token(user_id)
        with engine.begin() as conn:
            conn.execute(text('DELETE FROM users WHERE user_id = :u'), {'u': user_id})
        return token
    finally:
        engine.dispose()


#: Bodies for routes that validate before they authorise.
#:
#: FastAPI resolves a request body alongside its dependencies, so a route
#: with a required model can answer 422 before the authentication
#: dependency ever raises — and a 422 is not a refusal, it is "your body
#: was wrong", which tells us nothing about whether the caller was
#: checked. Those routes need a body that passes validation so the sweep
#: reaches the question it is asking.
#:
#: `POST /v1/chat/completions` is the only one today, and it is the one
#: that matters most: the live path, and the one `OrgContexts.resolve` opens
#: without a membership check when no organisation resolves.
_VALID_REQUESTS: dict[tuple[str, str], dict] = {
    ('POST', '/v1/chat/completions'): {
        'json': {'model': 'any-model', 'messages': [{'role': 'user', 'content': 'hello'}]}
    },
    # Anthropic's Messages API requires max_tokens as well.
    ('POST', '/v1/messages'): {
        'json': {'model': 'any-model', 'max_tokens': 16, 'messages': [{'role': 'user', 'content': 'hello'}]}
    },
    # Document search. `q` has a minimum length, so without one the
    # request is refused as malformed before anybody asks who sent it.
    # Lineage. Both bodies are constrained — a state that is not a
    # decision and a negative version are refused as malformed — so
    # without these the sweep never reaches the authorisation it is
    # testing.
    # Co-authoring. ``base_n`` has no default on purpose — a client
    # that forgot to track the version it wrote from must not silently
    # append to whatever the head happens to be — so the sweep has to
    # supply one or it never reaches the authorisation it is testing.
    # Filing. A folder needs a name, so the sweep supplies one or it
    # never gets past validation to the authorisation it is testing.
    # Writing the edit in progress. A draft is not a version — it is
    # what an edit lands in now — but writing one is the same
    # permission, so the sweep has to reach it.
    # Asking to be let in, and settling one. Both take a body, so
    # without one they answer 422 and the sweep never reaches the
    # authorisation it exists to test.
    # Widening a document's audience. The identity is required, so
    # without one this answers 422 and the sweep never reaches the
    # ownership check it exists to exercise.
    # Mints a *new* 30-day token from an old one, so a deleted user
    # reaching it would get another month on the strength of a token
    # that should already be dead.
    ('POST', '/v1/cli/token'): {'json': {'org_id': 'no-such-org'}},
}

#: Routes that will never answer 401, with the reason.
#:
#: Listed rather than skipped by a pattern, because "this route does not
#: refuse" is a claim that has to be justified one route at a time.
_DELIBERATELY_NOT_REFUSED: dict[tuple[str, str], str] = {}


def _fill(path: str) -> str:
    """Substitute path parameters with values that exist nowhere.

    The point is never to reach the handler: authorisation must refuse
    before the identifier is looked at, so a nonsense id is the right
    kind of id to send.
    """
    return re.sub(r'\{[^}]+\}', 'no-such-thing', path)


class TestTheSweep:
    async def test_every_user_authenticated_route_refuses_a_deleted_user(self, client):
        """The whole surface, from the router rather than from a list."""
        token = _create_then_delete_a_user()
        headers = {'Authorization': f'Bearer {token}'}

        routes = _user_authenticated_routes()
        assert len(routes) > 30, f'only {len(routes)} routes found — has the router changed shape?'

        allowed: list[str] = []
        unreached: list[str] = []
        for method, path in routes:
            if (method, path) in _DELIBERATELY_NOT_REFUSED:
                continue
            request = _VALID_REQUESTS.get((method, path), {})
            response = await client.request(
                method,
                _ABSOLUTE + _fill(path),
                headers=headers,
                json=request.get('json', {}),
                params=request.get('params'),
            )
            if response.status_code == 401:
                continue
            if response.status_code == 422:
                # The body was judged before the caller. Not a refusal,
                # so not a pass either — reported rather than swallowed,
                # because a silently skipped route is how a sweep comes
                # to cover less than it claims.
                unreached.append(f'{method} {path}')
                continue
            allowed.append(f'{method} {path} -> {response.status_code}')

        assert not unreached, (
            'these answered 422 before authorising, so this sweep never asked its question '
            f'about them: {unreached}. Add whatever makes the request '
            'valid to `_VALID_REQUESTS` so the route is actually tested.'
        )

        assert not allowed, (
            'these accepted a token for a user who has been deleted:\n  '
            + '\n  '.join(allowed)
            + '\n\nA signed token cannot be withdrawn before it expires, so removing the '
            'account is the only revocation an operator has. Every route that trusts a user '
            'token must resolve it through `app.security.subject.Subjects.require_existing_user`.'
        )

    def test_every_entry_in_both_maps_belongs_to_a_live_route(self):
        """No ghosts in either map.

        An entry for a route that no longer exists silently stops
        mattering while the sweep carries on looking complete — the same
        staleness F115 guards against for a different list.
        """
        live = set(_user_authenticated_routes())
        ghosts = sorted(key for key in {**_VALID_REQUESTS, **_DELIBERATELY_NOT_REFUSED} if key not in live)

        assert not ghosts, f'these name routes that do not exist: {ghosts}'

    async def test_a_live_user_is_not_refused_by_the_same_check(self, client, db):
        """The control. A check that refuses everybody would pass the
        sweep above and break the product."""
        from tests.conftest import create_org, create_user, make_token

        user = await create_user(db, email=f'live-{uuid.uuid4().hex[:6]}@example.com')
        org = await create_org(db, owner_user_id=str(user.user_id), slug=f'live-{uuid.uuid4().hex[:6]}')
        await db.commit()
        token = make_token(str(user.user_id), org_id=str(org.id), org_name='L')

        response = await client.get(_ABSOLUTE + '/v1/portal/me', headers={'Authorization': f'Bearer {token}'})

        assert response.status_code == 200, response.text


class TestTheTwoPathsThatWereWrong:
    """Named individually as well, because each failed differently."""

    async def test_the_chat_path_refuses(self, db):
        """The live path, and the one with no membership check to fall
        back on: `OrgContexts.resolve` returns without asserting membership
        when no org context resolves, so existence is the only
        authorisation there is."""
        from fastapi import HTTPException

        from app.services.auth.org_context import OrgContexts

        token = _create_then_delete_a_user()

        with pytest.raises(HTTPException) as caught:
            await OrgContexts().resolve(token)

        assert caught.value.status_code == 401

    async def test_the_portal_path_provisions_nothing(self, client):
        """It did not merely allow the request — it created an
        organisation for the deleted user and made them its admin."""
        token = _create_then_delete_a_user()
        claims = JWTService().decode_token(token)
        user_id = claims.user_id

        response = await client.get(_ABSOLUTE + '/v1/portal/me', headers={'Authorization': f'Bearer {token}'})
        assert response.status_code == 401, response.text

        engine = create_engine(TEST_DB_URL_SYNC)
        try:
            with engine.begin() as conn:
                orgs = conn.execute(
                    text('SELECT count(*) FROM organizations WHERE owner_user_id = :u'), {'u': user_id}
                ).scalar_one()
        finally:
            engine.dispose()

        assert orgs == 0, (
            f'{orgs} organisation(s) were created for a user who does not exist — the refused '
            'request still provisioned state'
        )

    async def test_the_refusal_says_nothing_about_why(self):
        """Same wording as a bad signature.

        AC-10's rule: the holder needs to know their token stopped
        working, and nothing beyond that. "No such user" would confirm an
        account was deleted to whoever is holding the token.
        """
        from fastapi import HTTPException

        from app.security.subject import Subjects

        with pytest.raises(HTTPException) as caught:
            # `issued_at` became required in B5: a caller without one
            # has not decoded a token and should not be asking.
            await Subjects().require_existing_user('nobody-at-all', issued_at=None)

        assert str(caught.value.detail) == 'Invalid or expired token'
