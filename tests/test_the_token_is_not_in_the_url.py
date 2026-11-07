"""A sign-in no longer puts a working token in a URL. F107.

Every OAuth and SSO callback used to redirect to
``/login?token=<jwt>``. That is a bearer credential in the browser's
history, in the ``Referer`` of anything the page then touches, and in the
access log of whatever proxies the portal — and F102 deliberately made
those logs machine-readable and shippable, which is good for everything
except this.

It is replaced by a one-time code, exchanged over ``POST`` for the token.
The code is still in the URL; it is worthless thirty seconds later and
worthless after one use, which is the point.

The three properties worth testing are the ones a plausible
implementation gets wrong: that the code really is single-use, that the
window really is short, and that no redirect anywhere still carries a
token.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from app.db.redis.auth_handoff import _TTL_SECONDS, AuthHandoffDB

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _code_without_comments(path: pathlib.Path) -> str:
    """The file's code with comment tokens removed.

    `tokenize` rather than a regex: `#` appears inside strings, and a
    regex that handles that correctly is a Python parser.
    """
    import io
    import tokenize

    pieces = []
    with path.open('rb') as handle:
        for token in tokenize.tokenize(io.BytesIO(handle.read()).readline):
            if token.type != tokenize.COMMENT:
                pieces.append(token.string)
    return ' '.join(pieces)


pytestmark = pytest.mark.anyio


@pytest.fixture
async def admin_with_org(db):
    """A real member of a real org, for the CLI handoff.

    `POST /v1/cli/token` verifies membership before it mints anything,
    so a bare token is not enough to reach the code path under test.
    """
    import uuid

    from tests.conftest import add_member, auth_header, create_org, create_user, make_token

    user = await create_user(db, email=f'handoff-{uuid.uuid4().hex[:6]}@example.com')
    org = await create_org(db, owner_user_id=str(user.user_id), slug=f'h-{uuid.uuid4().hex[:6]}')
    await add_member(db, str(org.id), str(user.user_id), role='admin')
    await db.commit()

    return {
        'headers': auth_header(make_token(str(user.user_id))),
        'org_id': str(org.id),
    }


class TestTheCodeIsSingleUse:
    async def test_it_redeems_once(self):
        handoff = AuthHandoffDB()
        code = await handoff.issue('the.jwt.value')

        assert await handoff.redeem(code) == 'the.jwt.value'

    async def test_a_second_redemption_gets_nothing(self):
        """The property the whole design rests on. Without it, a code in
        a proxy log is as good as the token was."""
        handoff = AuthHandoffDB()
        code = await handoff.issue('the.jwt.value')

        await handoff.redeem(code)

        assert await handoff.redeem(code) is None

    async def test_two_simultaneous_redemptions_yield_one_token(self):
        """`GETDEL`, not `GET` then `DELETE`. The two-step version lets
        both requests read the value before either deletes it — so a code
        observed in flight could be redeemed alongside the real exchange.

        Run as twenty concurrent attempts, because a race that needs two
        is rarely reproduced by two.
        """
        handoff = AuthHandoffDB()
        code = await handoff.issue('the.jwt.value')

        results = await asyncio.gather(*(handoff.redeem(code) for _ in range(20)))
        winners = [result for result in results if result]

        assert winners == ['the.jwt.value'], f'{len(winners)} redemptions succeeded, expected 1'

    async def test_an_unknown_code_is_not_an_error(self):
        """The endpoint turns this into a 400. The store just says no."""
        assert await AuthHandoffDB().redeem('never-issued') is None

    async def test_an_empty_code_is_refused_without_touching_redis(self):
        assert await AuthHandoffDB().redeem('') is None

    async def test_codes_are_unguessable(self):
        handoff = AuthHandoffDB()
        codes = {await handoff.issue('x') for _ in range(30)}

        assert len(codes) == 30
        assert all(len(code) >= 32 for code in codes)


class TestTheWindowIsShort:
    def test_the_ttl_is_seconds_not_minutes(self):
        """Nobody waits at this step: the browser follows one redirect
        and posts once. The TTL is the interval in which a code seen in a
        log is still worth something."""
        assert _TTL_SECONDS <= 60, f'{_TTL_SECONDS}s is long enough to matter'

    def test_it_is_shorter_than_the_oauth_state_window(self):
        """State has to survive a person taking their time at the
        provider's consent screen. This does not."""
        from app.db.redis.oauth_state import STATE_TTL_SECONDS as state_ttl

        assert _TTL_SECONDS < state_ttl

    async def test_the_code_expires(self, monkeypatch):
        """Verified against Redis rather than asserted about the
        constant, because `ex=` on the wrong call silently never
        expires."""
        monkeypatch.setattr('app.db.redis.auth_handoff._TTL_SECONDS', 1)

        handoff = AuthHandoffDB()
        code = await handoff.issue('the.jwt.value')

        await asyncio.sleep(1.2)

        assert await handoff.redeem(code) is None


class TestTheExchangeEndpoint:
    async def test_it_returns_the_token_for_a_valid_code(self, client):
        code = await AuthHandoffDB().issue('issued.for.this.test')

        response = await client.post('/auth/exchange', json={'code': code})

        assert response.status_code == 200, response.text
        assert response.json()['access_token'] == 'issued.for.this.test'

    async def test_the_code_does_not_work_twice_through_the_api(self, client):
        code = await AuthHandoffDB().issue('issued.for.this.test')

        assert (await client.post('/auth/exchange', json={'code': code})).status_code == 200
        assert (await client.post('/auth/exchange', json={'code': code})).status_code == 400

    async def test_an_unknown_code_is_refused(self, client):
        response = await client.post('/auth/exchange', json={'code': 'not-a-real-code'})

        assert response.status_code == 400, response.status_code

    async def test_expired_used_and_unknown_are_indistinguishable(self, client):
        """ "That code was valid a moment ago" is only useful to somebody
        probing."""
        code = await AuthHandoffDB().issue('x')
        await client.post('/auth/exchange', json={'code': code})

        used = await client.post('/auth/exchange', json={'code': code})
        unknown = await client.post('/auth/exchange', json={'code': 'never-issued-at-all'})

        assert used.status_code == unknown.status_code
        assert used.json()['detail'] == unknown.json()['detail']

    async def test_an_empty_code_is_a_validation_error(self, client):
        response = await client.post('/auth/exchange', json={'code': ''})

        assert response.status_code == 422, response.status_code

    async def test_it_is_not_a_get(self):
        """A `GET ?code=` would put the code in the URL — the problem
        being fixed — and make a token-bearing response cacheable."""
        source = (_ROOT / 'app/api/base_v1/endpoints/client/auth_exchange.py').read_text()

        assert '@router.post(' in source
        assert '@router.get(' not in source


class TestTheHandoffToTheDesktopApp:
    """The path F107 missed, and the reason it missed it.

    F107 removed `?token=<jwt>` from the browser sign-in redirect. The
    **desktop and CLI** handoff kept it: `CLIAuthPage.tsx` did

        window.location.href =
          `http://localhost:${port}/callback?token=${access_token}`

    which is a browser navigation carrying a working org-scoped 30-day
    credential — in the address bar, and in the history for as long as
    the profile lives. Exercised end to end before the fix: the flow
    worked, and put a 236-character JWT there on every login.

    The sweep below could not see it. It reads `app/api/**` for a
    Python `RedirectResponse` with a `token` query, and this URL was
    built in TypeScript. That is the fourth time in this review a rule
    has been aimed one directory away from the thing it was written to
    catch — F111's log rule, F114's audit prefixes, F115's column
    names. F124.
    """

    async def test_the_cli_token_endpoint_returns_a_code_as_well(self, client, admin_with_org):
        response = await client.post(
            '/cli/token', headers=admin_with_org['headers'], json={'org_id': admin_with_org['org_id']}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body['handoff_code'], 'no one-time code for the browser to carry'
        assert body['handoff_code'] != body['access_token']

    async def test_the_code_redeems_to_the_same_token(self, client, admin_with_org):
        """Otherwise the app signs in as somebody else, or as nobody."""
        minted = (
            await client.post(
                '/cli/token',
                headers=admin_with_org['headers'],
                json={'org_id': admin_with_org['org_id']},
            )
        ).json()

        exchanged = await client.post('/auth/exchange', json={'code': minted['handoff_code']})

        assert exchanged.status_code == 200, exchanged.text
        assert exchanged.json()['access_token'] == minted['access_token']

    async def test_the_code_works_once(self, client, admin_with_org):
        minted = (
            await client.post(
                '/cli/token',
                headers=admin_with_org['headers'],
                json={'org_id': admin_with_org['org_id']},
            )
        ).json()

        first = await client.post('/auth/exchange', json={'code': minted['handoff_code']})
        second = await client.post('/auth/exchange', json={'code': minted['handoff_code']})

        assert first.status_code == 200
        assert second.status_code == 400, 'the code was redeemable twice'

    def test_the_portal_never_puts_a_token_in_a_url(self):
        """The rule extended to where the offender actually lived.

        Not a check on `CLIAuthPage.tsx` — a check on every page. The
        previous rule named two Python files and swept a third
        directory; naming one more file would leave the next one out
        the same way.
        """
        portal = _ROOT / 'portal/src'
        offenders = []
        scanned = 0

        for path in portal.rglob('*.tsx'):
            if '__tests__' in str(path):
                continue
            scanned += 1
            for number, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(('//', '*', '/*')):
                    continue
                # A URL being built, with a token going into it. Both
                # halves matter: `access_token` appears legitimately
                # wherever a response is read.
                builds_url = 'location.href' in line or 'window.open' in line or 'href=`' in line
                if builds_url and ('token=' in line or 'access_token' in line):
                    offenders.append(f'{path.relative_to(_ROOT)}:{number} {stripped[:70]}')

        assert scanned > 20, f'only scanned {scanned} pages — has the portal moved?'
        assert not offenders, (
            'these put a token into a URL:\n  '
            + '\n  '.join(offenders)
            + '\n\nA URL is the address bar, the history and the referrer. Send a one-time code '
            'from `POST /v1/cli/token` and let the receiver exchange it at POST /v1/auth/exchange.'
        )


class TestNoRedirectCarriesAToken:
    """The rule, rather than the two call sites. A third provider added
    later, or a revert of either of these, fails here."""

    @pytest.mark.parametrize(
        'endpoint',
        [
            'app/api/base_v1/endpoints/client/oauth.py',
            'app/api/base_v1/endpoints/client/oidc.py',
        ],
    )
    def test_the_callback_redirects_with_a_code(self, endpoint):
        source = (_ROOT / endpoint).read_text()

        assert 'handoff.issue(' in source, f'{endpoint} does not issue a one-time code'
        assert "'token': result.token.access_token" not in source, (
            f'{endpoint} still puts the token in the redirect query'
        )

    def test_nothing_anywhere_builds_a_token_query_parameter(self):
        """Swept across the endpoints rather than the two known files.

        Comments are stripped before the scan. The first version was not,
        and matched the comment in `oauth.py` that explains what the old
        `?token=<jwt>` used to leak — a rule that reports the note about
        the bug as the bug. Third time in this review that a source sweep
        has matched prose: F102's rule flagged `OAUTH_REDIRECT_BASE_URL`
        for containing "AUTH", and F105's flagged a commented-out
        `proxy_pass`.
        """
        offenders = []

        for path in (_ROOT / 'app/api').rglob('*.py'):
            code = _code_without_comments(path)
            if 'RedirectResponse' not in code:
                continue
            if "'token':" in code or '"token":' in code:
                offenders.append(str(path.relative_to(_ROOT)))

        assert offenders != ['nothing scanned'], 'the scan found no files'
        assert not offenders, (
            f'these build a redirect with a token in the query: {offenders}. The token belongs '
            'in an exchange response body, not a URL.'
        )

    def test_the_sweep_can_actually_fail(self):
        """The scan above passes trivially if `_code_without_comments`
        returns nothing useful. This checks it still sees real code."""
        code = _code_without_comments(_ROOT / 'app/api/base_v1/endpoints/client/oauth.py')

        assert 'RedirectResponse' in code, 'the comment stripper ate the code'
        assert 'A one-time code, never the token' not in code, 'comments are not being stripped'

    def test_the_portal_exchanges_rather_than_reading_a_token(self):
        source = (_ROOT / 'portal/src/auth/LoginPage.tsx').read_text()

        assert "'/auth/exchange'" in source
        assert "searchParams.get('token')" not in source, 'the portal still reads a token from the URL'
