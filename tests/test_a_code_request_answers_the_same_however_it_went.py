"""`AuthEmails` and `CLITokens`, the services the auth endpoints delegate to, with their stores substituted.

A code request has one exit for its four outcomes — sent, throttled,
suppressed and no relay — and the answer must not tell them apart
(F112). The HTTP tests in `test_anonymous_email_cannot_be_abused.py`
and the sign-in and sign-up tests ask through the endpoints; here each
outcome is produced directly, including what is queued, logged and
recorded behind it.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import BackgroundTasks, HTTPException
import pytest

from app.security.api_key_sessions import REFUSED as API_KEY_SESSION_REFUSED
from app.services.auth.auth_emails import AuthEmails
from app.services.auth.cli_tokens import CLITokens
from app.services.auth.jwt_service import JWTService
from app.services.auth.otp_service import CodeSendThrottled, OTPResult
from tests.conftest import make_token

_NO_RELAY = 'Email delivery is not configured on this deployment.'
_SENT = 'Verification code has been sent to ada@acme.example'


class _Delivery:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.recorded: list[tuple[str, str, str]] = []
        self.noted: list[tuple[str, str]] = []

    def no_relay_message(self) -> str:
        return _NO_RELAY

    def record_undeliverable_code(self, *, purpose: str, email: str, code: str) -> None:
        self.recorded.append((purpose, email, code))

    def note_undeliverable(self, *, purpose: str, email: str) -> None:
        self.noted.append((purpose, email))


class _Mail:
    async def send_otp_code(self, *, to_email: str, code: str) -> bool:
        return True

    async def send_sign_up_code(self, *, to_email: str, code: str) -> bool:
        return True

    async def send_password_reset_code(self, *, to_email: str, code: str) -> bool:
        return True

    async def send_welcome(self, *, to_email: str, name: str | None = None) -> bool:
        return True


class _Codes:
    """Every issuer `AuthEmails` calls, answering one way."""

    def __init__(self, *, throttled: bool = False, suppressed: bool = False) -> None:
        self._throttled = throttled
        self._suppressed = suppressed

    async def _issue(self, email: str) -> OTPResult:
        if self._throttled:
            raise CodeSendThrottled(email)
        return OTPResult(message=_SENT, code='123456', email=email, suppress_delivery=self._suppressed)

    generate_signin_code = generate_password_reset_code = generate_address_code = send_sign_up_code = _issue


def _emails(delivery: _Delivery, codes: _Codes, mail: _Mail | None = None) -> AuthEmails:
    return AuthEmails(delivery=delivery, emails=mail or _Mail(), otp=codes, auth=codes)  # type: ignore[arg-type]


_SENDS = ['send_sign_in_code', 'send_sign_up_code', 'send_reset_password_code', 'send_company_address_code']
#: The doors an anonymous caller reaches. The company-address code is asked
#: for by a signed-in account about its own address and answers in its own
#: words (`test_a_person_belongs_to_their_domains_organisation.py`).
_ANONYMOUS_SENDS = ['send_sign_in_code', 'send_sign_up_code', 'send_reset_password_code']


class TestEveryOutcomeAnswersAlike:
    @pytest.mark.parametrize('send', _SENDS)
    async def test_a_sent_code_is_queued_for_the_relay(self, send):
        bg, delivery = BackgroundTasks(), _Delivery(enabled=True)

        message = await getattr(_emails(delivery, _Codes()), send)(bg, 'ada@acme.example')

        assert message == _SENT
        assert [task.kwargs for task in bg.tasks] == [{'to_email': 'ada@acme.example', 'code': '123456'}]
        assert delivery.recorded == []

    @pytest.mark.parametrize(
        ('send', 'template'),
        [
            ('send_sign_in_code', 'send_otp_code'),
            ('send_sign_up_code', 'send_sign_up_code'),
            ('send_reset_password_code', 'send_password_reset_code'),
            ('send_company_address_code', 'send_sign_up_code'),
        ],
    )
    async def test_each_code_goes_out_in_its_own_email(self, send, template):
        """A reset code in a sign-in email would tell the reader to sign in with it."""
        bg, mail = BackgroundTasks(), _Mail()

        await getattr(_emails(_Delivery(enabled=True), _Codes(), mail), send)(bg, 'ada@acme.example')

        assert [task.func for task in bg.tasks] == [getattr(mail, template)]

    @pytest.mark.parametrize('send', _ANONYMOUS_SENDS)
    async def test_throttled_and_suppressed_answer_as_a_sent_one_does_and_queue_nothing(self, send):
        for codes in (_Codes(throttled=True), _Codes(suppressed=True)):
            bg = BackgroundTasks()

            message = await getattr(_emails(_Delivery(enabled=True), codes), send)(bg, 'ada@acme.example')

            assert message == _SENT
            assert bg.tasks == []

    @pytest.mark.parametrize('send', _ANONYMOUS_SENDS)
    async def test_with_no_relay_everybody_is_told_so_and_the_code_never_answers(self, send):
        """Sent, throttled and suppressed alike. Only a code actually
        made for somebody goes to `record_undeliverable_code` (logged only
        when the operator opted in), and never into the answer."""
        purposes = {
            'send_sign_in_code': 'sign-in',
            'send_sign_up_code': 'sign-up',
            'send_reset_password_code': 'password-reset',
        }
        answers, recorded = [], []
        for codes in (_Codes(), _Codes(throttled=True), _Codes(suppressed=True)):
            bg, delivery = BackgroundTasks(), _Delivery(enabled=False)
            answers.append(await getattr(_emails(delivery, codes), send)(bg, 'ada@acme.example'))
            recorded.append(delivery.recorded)
            assert bg.tasks == []

        assert answers == [_NO_RELAY] * 3
        assert recorded == [[(purposes[send], 'ada@acme.example', '123456')], [], []]


class TestTheWelcome:
    def test_it_is_queued_when_mail_can_go(self):
        bg, delivery = BackgroundTasks(), _Delivery(enabled=True)

        _emails(delivery, _Codes()).welcome(bg, email='ada@acme.example', name='Ada')

        assert [(task.func.__name__, task.kwargs) for task in bg.tasks] == [
            ('send_welcome', {'to_email': 'ada@acme.example', 'name': 'Ada'})
        ]

    def test_without_a_relay_it_is_only_noted(self):
        bg, delivery = BackgroundTasks(), _Delivery(enabled=False)

        _emails(delivery, _Codes()).welcome(bg, email='ada@acme.example')

        assert bg.tasks == []
        assert delivery.noted == [('welcome', 'ada@acme.example')]


class _Subjects:
    def __init__(self, *, gone: bool = False) -> None:
        self._gone = gone

    async def resolve_existing_user_id(self, token: str) -> str:
        if self._gone:
            raise HTTPException(401, detail='User not found')
        return 'u-1'


class _Team:
    def __init__(self, *, member: bool) -> None:
        self._member = member

    async def get_active_member(self, org_id: str, user_id: str) -> object | None:
        return SimpleNamespace(user_id=user_id) if self._member else None


class _Orgs:
    def __init__(self, *, exists: bool = True) -> None:
        self._exists = exists

    async def get_by_id(self, org_id: str) -> object | None:
        return SimpleNamespace(display_name='Acme') if self._exists else None


class _Groups:
    async def group_of(self, user_id: str, org_id: str) -> str:
        return 'g-legal'


class _Jwt:
    def __init__(self) -> None:
        self.minted: list[dict[str, object]] = []

    def create_token(self, user_id: str, **claims: object) -> str:
        self.minted.append({'user_id': user_id, **claims})
        return 'the-cli-token'


class _Handoff:
    async def issue(self, token: str) -> str:
        return f'code-for-{token}'


class _Users:
    def __init__(self) -> None:
        self.signed_in: list[str] = []

    async def note_sign_in(self, user_id: str) -> None:
        self.signed_in.append(user_id)


def _tokens(
    *,
    member: bool = True,
    org_exists: bool = True,
    gone: bool = False,
    jwt: _Jwt | None = None,
    users: _Users | None = None,
) -> CLITokens:
    return CLITokens(
        team_db=_Team(member=member),  # type: ignore[arg-type]
        org_db=_Orgs(exists=org_exists),  # type: ignore[arg-type]
        groups=_Groups(),  # type: ignore[arg-type]
        jwt=jwt or _Jwt(),  # type: ignore[arg-type]
        handoff=_Handoff(),  # type: ignore[arg-type]
        subjects=_Subjects(gone=gone),  # type: ignore[arg-type]
        users=users or _Users(),  # type: ignore[arg-type]
    )


class TestTheCLIToken:
    async def test_a_member_gets_an_org_scoped_token_and_a_handoff_code_for_it(self):
        jwt, users = _Jwt(), _Users()

        issued = await _tokens(jwt=jwt, users=users).issue(token=make_token('u-1'), org_id='org-1')

        assert (issued.access_token, issued.handoff_code) == ('the-cli-token', 'code-for-the-cli-token')
        assert jwt.minted == [{'user_id': 'u-1', 'org_id': 'org-1', 'org_name': 'Acme', 'group': 'g-legal'}]
        assert users.signed_in == ['u-1']

    async def test_someone_who_is_not_a_member_gets_nothing(self):
        jwt, users = _Jwt(), _Users()

        with pytest.raises(HTTPException) as refused:
            await _tokens(member=False, jwt=jwt, users=users).issue(token=make_token('u-1'), org_id='org-1')

        assert (refused.value.status_code, refused.value.detail) == (403, 'Not a member of this organization')
        assert jwt.minted == [] and users.signed_in == []

    async def test_an_organisation_with_no_row_gives_a_token_with_no_name(self):
        jwt = _Jwt()

        issued = await _tokens(org_exists=False, jwt=jwt).issue(token=make_token('u-1'), org_id='org-1')

        assert issued.access_token == 'the-cli-token'
        assert jwt.minted == [{'user_id': 'u-1', 'org_id': 'org-1', 'org_name': None, 'group': 'g-legal'}]

    async def test_a_session_an_api_key_bought_is_refused_before_membership_is_asked(self):
        """Not a member either, and still told about the key: 30 days are
        not minted from 15 minutes whoever holds them (§22)."""
        jwt, users = _Jwt(), _Users()
        session = JWTService().create_token('u-1', api_key_id='key-1')

        with pytest.raises(HTTPException) as refused:
            await _tokens(member=False, jwt=jwt, users=users).issue(token=session, org_id='org-1')

        assert (refused.value.status_code, refused.value.detail) == (403, API_KEY_SESSION_REFUSED)
        assert jwt.minted == [] and users.signed_in == []

    async def test_somebody_who_no_longer_exists_is_refused_first_even_on_a_key_session(self):
        jwt, users = _Jwt(), _Users()
        session = JWTService().create_token('u-1', api_key_id='key-1')

        with pytest.raises(HTTPException) as refused:
            await _tokens(gone=True, jwt=jwt, users=users).issue(token=session, org_id='org-1')

        assert refused.value.status_code == 401
        assert jwt.minted == [] and users.signed_in == []
