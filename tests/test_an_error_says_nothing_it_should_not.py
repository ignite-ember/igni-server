"""What a failure tells the caller.

F120. `AuthService.sign_up` caught bare `Exception` and put `str(e)` in
the response body of an **unauthenticated** route. Demonstrated against a
real database by breaking the `users` INSERT — an anonymous caller
received:

    HTTP 400, 484 chars:
    Failed to create user: (…asyncpg.ProgrammingError) …
    [SQL: SELECT users.id, users.user_id, users.email, users.hashed_password,
          users.full_name, users.auth_provider, users.created_at, … ]
    [parameters: ('victim@example.com',)]
    (Background on this error at: https://sqlalche.me/e/20/f405)

Every column of `users` including `hashed_password`, the statement, the
parameters, and a version-pinned link naming the ORM and the dialect. On
a 400, which also told the caller they had done something wrong.

## The rule, and why nine other sites are not offenders

Nine other places interpolate a caught exception into a response detail,
and every one of them catches a **named** exception whose message was
written for whoever receives it — `ModelNotAvailableError` lists the
models this deployment serves, `SecretCipherUnavailable` names the
environment variable to set. Those are the API, not a leak.

The distinction is not the length of the string or the status code. It is
**who wrote the message**. A domain exception's text is part of the
contract; a `ProgrammingError` from asyncpg was written for somebody
debugging asyncpg.

So the rule is about what was *caught*: a broad handler — `except
Exception`, `except BaseException`, or a bare `except` — must not
describe itself to a caller. That leaves the nine alone without an
exemption list to argue over, and `app/utils/internal_failure.py` is the
sanctioned way through it.
"""

from __future__ import annotations

import ast
import logging
import pathlib

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

_APP = pathlib.Path(__file__).resolve().parent.parent / 'app'

_BROAD = {'Exception', 'BaseException'}


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """A handler that catches things nobody anticipated."""
    if handler.type is None:
        return True
    return any(node.id in _BROAD for node in ast.walk(handler.type) if isinstance(node, ast.Name))


def _detail_expressions(call: ast.Call, source: str) -> list[ast.expr]:
    """Every expression this call uses as a client-visible `detail`.

    Both spellings: `HTTPException(detail=...)` and a response body built
    as `{'detail': ...}`, because the second is how somebody would write
    it after being told not to write the first.
    """
    found: list[ast.expr] = []
    for keyword in call.keywords:
        if keyword.arg == 'detail':
            found.append(keyword.value)
    for node in ast.walk(call):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == 'detail':
                    found.append(value)
    return found


def _scan() -> tuple[list[str], list[str]]:
    """`(from a broad catch, from a named catch)`, as readable locations."""
    broad: list[str] = []
    named: list[str] = []

    class Finder(ast.NodeVisitor):
        def __init__(self, path: pathlib.Path, source: str) -> None:
            self.path, self.source, self.stack = path, source, []

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            self.stack.append((node.name, _is_broad(node), node.type))
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            for expression in _detail_expressions(node, self.source):
                used = {n.id for n in ast.walk(expression) if isinstance(n, ast.Name)}
                for name, is_broad, caught in self.stack:
                    if not name or name not in used:
                        continue
                    caught_text = ast.get_source_segment(self.source, caught) if caught else 'bare except'
                    where = (
                        f'{self.path.relative_to(_APP.parent)}:{node.lineno} '
                        f'except {caught_text} -> detail includes {name!r}'
                    )
                    (broad if is_broad else named).append(where)
            self.generic_visit(node)

    for path in sorted(_APP.rglob('*.py')):
        source = path.read_text()
        Finder(path, source).visit(ast.parse(source))

    return broad, named


class TestTheRule:
    def test_no_broad_catch_describes_itself_to_a_caller(self):
        broad, _ = _scan()

        assert not broad, (
            'these hand a caller an exception nobody wrote for them:\n  '
            + '\n  '.join(broad)
            + '\n\nA broad handler catches whatever happened, and what happened may be a '
            'database error carrying the failing SQL, the schema and the bound parameters — '
            'which is what an anonymous sign-up used to receive. Use '
            '`app.utils.internal_failure.internal_failure`, which logs the traceback with a '
            'reference and returns only that reference. If the exception really is safe to '
            'return, catch it by name.'
        )

    def test_the_scan_still_finds_the_legitimate_ones(self):
        """Assert the scan measured something.

        Every check above is "this list is empty", so a scan that parsed
        nothing would pass in silence. The named list is the control: it
        should hold the nine sites whose messages are deliberately part
        of the API.
        """
        broad, named = _scan()

        assert len(list(_APP.rglob('*.py'))) > 150, 'the scan found almost no modules'
        assert len(named) >= 4, (
            f'only found {len(named)} named-exception details: {named}. This is the control '
            'for the check above — if it drops to zero, that check is passing because the '
            'scan sees nothing, not because the application is clean.'
        )
        assert not broad

    def test_a_broad_catch_would_be_caught(self):
        """The rule against source it is meant to reject.

        Written out rather than trusted, because six earlier rules in
        this review passed against code they could not actually see.
        """
        source = (
            'from fastapi import HTTPException\n'
            'def f():\n'
            '    try:\n'
            '        pass\n'
            '    except Exception as exc:\n'
            '        raise HTTPException(status_code=500, detail=str(exc))\n'
        )
        tree = ast.parse(source)
        handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
        assert _is_broad(handler)

        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and n.keywords)
        details = _detail_expressions(call, source)
        assert details, 'the detail keyword was not recognised'
        assert 'exc' in {n.id for n in ast.walk(details[0]) if isinstance(n, ast.Name)}

    def test_a_response_body_dict_is_caught_too(self):
        """`{'detail': str(exc)}` is the obvious way round the first
        spelling, so it has to be the same rule."""
        source = (
            'def f():\n'
            '    try:\n'
            '        pass\n'
            '    except Exception as exc:\n'
            "        return JSONResponse(content={'detail': str(exc)})\n"
        )
        call = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name))
        assert _detail_expressions(call, source), 'a detail in a dict body was not recognised'

    def test_the_application_is_not_built_in_debug_mode(self):
        """One keyword away from returning a traceback to everybody.

        Starlette's `ServerErrorMiddleware` renders the full traceback as
        the response body when `debug` is on. Nothing sets it — which is
        exactly why it is worth pinning: the finding above was about one
        handler, and this would be every handler at once, for any
        unhandled exception, including on unauthenticated routes.
        """
        from app.main import app

        assert app.debug is False, (
            'the FastAPI app is in debug mode, so Starlette answers every unhandled '
            'exception with its traceback — file paths, local variables and all — to '
            'whoever made the request'
        )

    def test_an_unhandled_exception_returns_no_traceback(self, client):
        """Asserted through the stack rather than from the flag.

        `app.debug` being False is the mechanism; this is the property.
        """
        import httpx

        from app.main import app

        @app.get('/_test_explode', include_in_schema=False)
        async def explode() -> None:
            raise RuntimeError('a secret in a variable: postgresql://user:hunter2@db/igni')

        try:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async_client = httpx.AsyncClient(transport=transport, base_url='http://test')
            response = _run(async_client.get('/_test_explode'))
        finally:
            app.router.routes = [r for r in app.router.routes if getattr(r, 'path', '') != '/_test_explode']

        assert response.status_code == 500
        body = response.text
        assert 'hunter2' not in body, body[:400]
        assert 'Traceback' not in body, body[:400]
        assert 'RuntimeError' not in body, body[:400]


def _run(coro):
    """Run one coroutine from a synchronous test.

    These two checks are about how the app is *constructed*, so they do
    not want the `anyio` fixtures — but the ASGI transport still needs a
    loop.
    """
    import asyncio

    return asyncio.run(coro)


class TestTheBehaviour:
    """The rule is static. This is what it protects, end to end."""

    @pytest.fixture
    async def broken_users_insert(self):
        """Break the `users` INSERT for real, then put it back.

        The column is renamed rather than the failure being faked,
        because the finding is about what a *genuine* SQLAlchemy error
        contains — and a mock cannot be wrong about that in the direction
        that matters.
        """
        from app.db.sql import SQLAlchemySessionManager

        async with SQLAlchemySessionManager().session_scope() as session:
            await session.execute(text('ALTER TABLE users RENAME COLUMN full_name TO full_name_moved'))
        try:
            yield
        finally:
            async with SQLAlchemySessionManager().session_scope() as session:
                await session.execute(text('ALTER TABLE users RENAME COLUMN full_name_moved TO full_name'))

    async def test_a_database_fault_during_sign_up_leaks_nothing(self, broken_users_insert, monkeypatch, caplog):
        from fastapi import HTTPException

        from app.api.base_v1.schema.auth import SignUpRequest
        from app.services.auth.auth_service import AuthService
        from app.services.auth.otp_service import OTPService

        async def accept_any_code(_self, **kwargs):
            # Takes `self`: `validate_signup_code` is an instance method
            # now, matching `UserDB.create` faked two lines down.
            return None

        monkeypatch.setattr(OTPService, 'validate_signup_code', accept_any_code)

        body = SignUpRequest(
            email='victim@example.com',
            code='123456',
            password='Str0ng-Passw0rd!',
            first_name='Jane',
            last_name='Doe',
        )

        with caplog.at_level(logging.ERROR):
            with pytest.raises(HTTPException) as caught:
                await AuthService.sign_up(body)

        detail = str(caught.value.detail)

        # Not the caller's fault, so not a 4xx.
        assert caught.value.status_code == 500, caught.value.status_code

        # Nothing about the database, the schema or the query.
        for forbidden in ('SQL', 'users.', 'hashed_password', 'asyncpg', 'sqlalche.me', 'parameters'):
            assert forbidden not in detail, f'{forbidden!r} reached the caller: {detail}'
        assert len(detail) < 200, f'{len(detail)} characters of detail: {detail}'

        # But it is traceable: the reference the caller was given appears
        # in the log, with the traceback beside it.
        reference = detail.split('Reference ')[1].split(' ')[0]
        assert len(reference) == 8, reference
        logged = '\n'.join(record.getMessage() for record in caplog.records)
        assert reference in logged, f'reference {reference} is in no log line'
        assert any(record.exc_info for record in caplog.records), (
            'the traceback was not logged, so the reference points at a line saying only '
            'that something failed — which the caller already knew'
        )

    async def test_a_duplicate_address_is_still_the_callers_problem(self, monkeypatch):
        """The pass-through must survive.

        `UserDB.create` raises `AddressTaken` for an address already taken,
        and that one *is* the caller's to fix. Turning every failure into
        an opaque 500 would have been the easy over-correction.
        """
        from fastapi import HTTPException

        from app.api.base_v1.schema.auth import SignUpRequest
        from app.db.sql.schemas.account_errors import AddressTaken
        from app.db.sql.user import UserDB
        from app.services.auth.auth_service import AuthService
        from app.services.auth.otp_service import OTPService

        async def accept_any_code(_self, **kwargs):
            # Takes `self`: `validate_signup_code` is an instance method
            # now, matching `UserDB.create` faked two lines down.
            return None

        async def already_taken(self, email, **kwargs):
            raise AddressTaken('That address is already registered')

        monkeypatch.setattr(OTPService, 'validate_signup_code', accept_any_code)
        monkeypatch.setattr(UserDB, 'create', already_taken)

        body = SignUpRequest(
            email='taken@example.com',
            code='123456',
            password='Str0ng-Passw0rd!',
            first_name='Jane',
            last_name='Doe',
        )

        with pytest.raises(HTTPException) as caught:
            await AuthService.sign_up(body)

        assert caught.value.status_code == 409
        assert 'already registered' in str(caught.value.detail)

    async def test_two_failures_get_different_references(self):
        """Not derived from the request.

        A reference computed from the email would be stable across
        attempts, which is convenient until it becomes a way to test
        whether two failures came from the same address.
        """
        from app.utils.internal_failure import internal_failure

        logger = logging.getLogger('test.internal_failure')
        first = internal_failure(ValueError('a'), doing='doing a thing', logger=logger)
        second = internal_failure(ValueError('a'), doing='doing a thing', logger=logger)

        assert str(first.detail) != str(second.detail)
