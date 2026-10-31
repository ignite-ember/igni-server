"""Nothing but the probes lives outside `/v1`.

`POST /token` sat at the app root: an OAuth2 password-form login,
`tags=['Token']`, described as "This endpoint allows users to log in and
obtain an access token for authentication." It called
`UserDB.verify_password`, which does not exist on `UserDB` — so every
request to it raised `AttributeError` and answered 500, for as long as it
had existed. Verified against a running server: three different bodies,
three 500s (and 422 for an empty one, from form validation before the
handler).

Nothing referenced it — not the portal, not the CLI, not a test — which is
why it survived. `POST /v1/sign-in` is the real password login and works.

Removed rather than repaired, since repairing it would have produced a
second, unversioned way to do the same thing. This file pins the root
surface so the next unversioned endpoint has to be deliberate.
"""

from __future__ import annotations

#: Everything legitimately outside the versioned API.
#:
#: The probes are root-mounted on purpose: an orchestrator's liveness
#: check should not move when the API version does.
#:
#: `/docs`, `/redoc` and `/openapi.json` do not appear here because they
#: are not part of the OpenAPI schema this reads, and they are registered
#: only when `app_env == 'dev'` in any case.
ALLOWED_ROOT_PATHS = {
    '/health',
    '/ready',
    '/version',
    # SCIM 2.0. The path is fixed by RFC 7644 — a provider is
    # configured with a base URL and appends `/Users` itself — so it is
    # not ours to version, and putting it under `/v1` would mean every
    # customer's Okta or Entra pointing at a path the standard does not
    # describe. Guarded by SCIM_TOKEN and 503 throughout when that is
    # unset; the sweep in `test_a_deleted_user_cannot_come_back.py`
    # excludes it for the same reason.
    '/scim/v2/ServiceProviderConfig',
    '/scim/v2/Users',
    '/scim/v2/Users/{user_id}',
    # Discovery. RFC 8414 and OIDC Discovery fix these paths: a service
    # deployed with only this server's URL appends them itself, so they
    # are not ours to version either. Public by nature — metadata and
    # public keys. See docs/service-identity.md.
    '/.well-known/openid-configuration',
    '/.well-known/jwks.json',
}


def _all_paths() -> set[str]:
    """Every registered path, read from the OpenAPI schema.

    Not `app.routes`: that holds an `_IncludedRouter` for the `/v1` mount,
    and neither a flat comprehension nor recursing into `.routes` reaches
    its children — both saw the three root probes and nothing else, which
    would let the allowlist check below pass while the versioned API was
    invisible to it. `app.openapi()` is the authoritative surface and works
    regardless of how routers nest.
    """
    from app.main import app

    return set(app.openapi()['paths'])


def _root_paths() -> set[str]:
    return {p for p in _all_paths() if not p.startswith('/v1')}


class TestTheRootSurface:
    def test_no_unversioned_endpoint_has_appeared(self):
        unexpected = _root_paths() - ALLOWED_ROOT_PATHS

        assert not unexpected, (
            f'unversioned endpoints outside /v1: {sorted(unexpected)}. Mount new '
            f'routes under /v1 — the last root-level one was a login endpoint that '
            f'500d on every request and that nothing referenced, so nobody noticed.'
        )

    def test_token_specifically_is_gone(self):
        """Named, because it is the one that was broken and because a
        reader of the removal commit will come looking for it."""
        assert '/token' not in _root_paths()

    def test_the_probes_are_still_mounted(self):
        """The allowlist must not be satisfiable by removing everything."""
        assert {'/health', '/ready', '/version'} <= _root_paths()


class TestTheRealLoginStillExists:
    """The removal is only correct because the versioned one works."""

    def test_sign_in_is_registered_under_v1(self):
        paths = _all_paths()

        assert '/v1/sign-in' in paths, sorted(p for p in paths if 'sign' in p)

    def test_userdb_still_has_no_verify_password(self):
        """The method the removed endpoint called. If it ever appears,
        this comment is the record of why `/token` was deleted rather
        than pointed at it.
        """
        from app.db.sql.user import UserDB

        assert not hasattr(UserDB, 'verify_password')
