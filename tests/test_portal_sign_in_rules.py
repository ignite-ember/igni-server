"""`PortalSignIn`'s rules, with its stores substituted, so no database or Redis is needed."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

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
)

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


def _sign_in(*, approved: bool = True, codes: _Codes | None = None) -> PortalSignIn:
    client = SimpleNamespace(client_id='svc', name='CodeIndex', redirect_uris=[REDIRECT_URI]) if approved else None
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
