"""`PortalSignIn`'s rules, with its stores substituted, so no database or Redis is needed."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
import pytest

from app.api.base_v1.schema.portal.authorize import Authorize
from app.api.base_v1.schema.portal.user import PortalAccount
from app.db.redis.authorization_codes import PendingAuthorization
from app.services.identity.portal_sign_in import (
    ChallengeMethodRefused,
    ClientNotApproved,
    NotAnOrganisationMember,
    PortalSignIn,
    RedirectNotRegistered,
    SignInRefused,
)
from app.services.identity.sign_in_refusals import SignInRefusals

REDIRECT_URI = 'https://codeindex.example.com/cb?tenant=acme'
CHALLENGE = 'c' * 43


class _Registry:
    def __init__(self, client: object | None) -> None:
        self._client = client

    async def approved(self, client_id: str) -> object | None:
        return self._client


class _Organisations:
    async def list_user_orgs(self, user_id: str) -> list[tuple[object, object]]:
        return [(SimpleNamespace(id='org-1', display_name='Acme'), object())]


class _Codes:
    def __init__(self) -> None:
        self.issued: list[PendingAuthorization] = []

    async def issue(self, pending: PendingAuthorization) -> str:
        self.issued.append(pending)
        return 'the-code'


def _sign_in(
    *, approved: bool = True, codes: _Codes | None = None, redirect_uris: list[str] | None = [REDIRECT_URI]
) -> PortalSignIn:
    client = SimpleNamespace(client_id='svc', name='CodeIndex', redirect_uris=redirect_uris) if approved else None
    return PortalSignIn(registry=_Registry(client), organisations=_Organisations(), codes=codes or _Codes())  # type: ignore[arg-type]


def _request(**overrides: str) -> Authorize:
    fields = {
        'client_id': 'svc',
        'redirect_uri': REDIRECT_URI,
        'organisation': 'org-1',
        'code_challenge': CHALLENGE,
        'state': 'xyz',
    }
    return Authorize(**{**fields, **overrides})


ACCOUNT = PortalAccount(user_id='user-1', session_issued_at=1700000000)


async def test_it_describes_the_client_and_the_organisations():
    ask = await _sign_in().describe(ACCOUNT, client_id='svc', redirect_uri=REDIRECT_URI)

    assert ask.model_dump() == {'client_name': 'CodeIndex', 'organisations': [{'org_id': 'org-1', 'name': 'Acme'}]}


async def test_the_code_is_bound_to_the_choice_and_appended_to_the_redirect():
    codes = _Codes()

    redirect = await _sign_in(codes=codes).authorize(ACCOUNT, _request())

    query = parse_qs(urlsplit(redirect.redirect_to).query)
    assert query == {'tenant': ['acme'], 'code': ['the-code'], 'state': ['xyz']}
    assert codes.issued == [
        PendingAuthorization(
            client_id='svc',
            redirect_uri=REDIRECT_URI,
            code_challenge=CHALLENGE,
            user_id='user-1',
            org_id='org-1',
            session_issued_at=1700000000,
        )
    ]


@pytest.mark.parametrize(
    ('sign_in', 'request_', 'refusal'),
    [
        (lambda: _sign_in(approved=False), _request(), ClientNotApproved),
        (_sign_in, _request(redirect_uri='https://evil.example.com/cb'), RedirectNotRegistered),
        (_sign_in, _request(code_challenge_method='plain'), ChallengeMethodRefused),
        (_sign_in, _request(organisation='org-2'), NotAnOrganisationMember),
    ],
)
async def test_each_rule_refuses(sign_in, request_, refusal):
    with pytest.raises(refusal):
        await sign_in().authorize(ACCOUNT, request_)


async def test_a_redirect_without_a_query_gets_one_and_an_empty_state_is_left_out():
    bare = 'https://codeindex.example.com/cb'

    redirect = await _sign_in(redirect_uris=[bare]).authorize(ACCOUNT, _request(redirect_uri=bare, state=''))

    assert redirect.redirect_to == f'{bare}?code=the-code'


async def test_a_session_with_no_issue_time_is_bound_to_now():
    codes = _Codes()
    before = int(datetime.now(timezone.utc).timestamp())

    await _sign_in(codes=codes).authorize(PortalAccount(user_id='user-1'), _request())

    assert before <= codes.issued[0].session_issued_at <= int(datetime.now(timezone.utc).timestamp())


@pytest.mark.parametrize(
    ('sign_in', 'redirect_uri', 'refusal'),
    [
        (lambda: _sign_in(approved=False), REDIRECT_URI, ClientNotApproved),
        (_sign_in, 'https://evil.example.com/cb', RedirectNotRegistered),
        (lambda: _sign_in(redirect_uris=None), REDIRECT_URI, RedirectNotRegistered),
    ],
)
async def test_the_page_is_refused_before_the_person_chooses(sign_in, redirect_uri, refusal):
    with pytest.raises(refusal):
        await sign_in().describe(ACCOUNT, client_id='svc', redirect_uri=redirect_uri)


@pytest.mark.parametrize(
    ('sign_in', 'request_', 'refusal'),
    [
        (
            lambda **kw: _sign_in(approved=False, **kw),
            _request(redirect_uri='https://evil.example.com/cb', code_challenge_method='plain', organisation='org-2'),
            ClientNotApproved,
        ),
        (
            _sign_in,
            _request(redirect_uri='https://evil.example.com/cb', code_challenge_method='plain', organisation='org-2'),
            RedirectNotRegistered,
        ),
        (_sign_in, _request(code_challenge_method='plain', organisation='org-2'), ChallengeMethodRefused),
    ],
    ids=['client-first', 'then-redirect', 'then-method'],
)
async def test_the_first_rule_broken_is_the_one_given_and_no_code_is_issued(sign_in, request_, refusal):
    codes = _Codes()

    with pytest.raises(refusal):
        await sign_in(codes=codes).authorize(ACCOUNT, request_)

    assert codes.issued == []


@pytest.mark.parametrize(
    ('refusal', 'status_code', 'detail'),
    [
        (ClientNotApproved(), 404, 'This application is not approved here'),
        (RedirectNotRegistered(), 400, 'This redirect address was not registered by the application'),
        (ChallengeMethodRefused(), 400, 'Only S256 code challenges are accepted'),
        (NotAnOrganisationMember(), 403, 'Not a member of this organisation'),
    ],
)
def test_each_refusal_is_the_portals_answer(refusal, status_code, detail):
    """`SignInRefusals` gives the status and the words the authorize page has always shown."""
    with pytest.raises(HTTPException) as raised, SignInRefusals():
        raise refusal

    assert (raised.value.status_code, raised.value.detail) == (status_code, detail)


def test_anything_else_passes_through_the_refusals():
    with pytest.raises(RuntimeError, match='redis is down'), SignInRefusals():
        raise RuntimeError('redis is down')


def test_a_refusal_it_has_no_answer_for_is_not_given_one():
    """Only the four known refusals become a 4xx; anything else is left to be the server error it is."""
    with pytest.raises(SignInRefused), SignInRefusals():
        raise SignInRefused('unforeseen')
