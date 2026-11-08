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

from app.services.auth.auth_emails import AuthEmails
from app.services.auth.cli_tokens import CLITokens
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


def _emails(delivery: _Delivery, codes: _Codes) -> AuthEmails:
    return AuthEmails(delivery=delivery, emails=_Mail(), otp=codes, auth=codes)  # type: ignore[arg-type]


_SENDS = ['send_sign_in_code', 'send_sign_up_code', 'send_reset_password_code', 'send_company_address_code']


class TestEveryOutcomeAnswersAlike:
    @pytest.mark.parametrize('send', _SENDS)
    async def test_a_sent_code_is_queued_for_the_relay(self, send):
        bg, delivery = BackgroundTasks(), _Delivery(enabled=True)

        message = await getattr(_emails(delivery, _Codes()), send)(bg, 'ada@acme.example')

        assert message == _SENT
        assert [task.kwargs for task in bg.tasks] == [{'to_email': 'ada@acme.example', 'code': '123456'}]
        assert delivery.recorded == []

    @pytest.mark.parametrize('send', _SENDS)
    async def test_throttled_and_suppressed_answer_as_a_sent_one_does_and_queue_nothing(self, send):
        for codes in (_Codes(throttled=True), _Codes(suppressed=True)):
            bg = BackgroundTasks()

            message = await getattr(_emails(_Delivery(enabled=True), codes), send)(bg, 'ada@acme.example')

            assert message == _SENT
            assert bg.tasks == []

    @pytest.mark.parametrize('send', _SENDS)
    async def test_with_no_relay_everybody_is_told_so_and_the_code_never_answers(self, send):
        """Sent, throttled and suppressed alike. Only a code actually
        made for somebody goes to `record_undeliverable_code` (logged only
        when the operator opted in), and never into the answer."""
        purposes = {
            'send_sign_in_code': 'sign-in',
            'send_sign_up_code': 'sign-up',
            'send_reset_password_code': 'password-reset',
            'send_company_address_code': 'company-address',
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

        assert [task.kwargs for task in bg.tasks] == [{'to_email': 'ada@acme.example', 'name': 'Ada'}]

    def test_without_a_relay_it_is_only_noted(self):
        bg, delivery = BackgroundTasks(), _Delivery(enabled=False)

        _emails(delivery, _Codes()).welcome(bg, email='ada@acme.example')

        assert bg.tasks == []
        assert delivery.noted == [('welcome', 'ada@acme.example')]


class _Subjects:
    async def resolve_existing_user_id(self, token: str) -> str:
        return 'u-1'


class _Team:
    def __init__(self, *, member: bool) -> None:
        self._member = member

    async def get_active_member(self, org_id: str, user_id: str) -> object | None:
        return SimpleNamespace(user_id=user_id) if self._member else None


class _Orgs:
    async def get_by_id(self, org_id: str) -> object:
        return SimpleNamespace(display_name='Acme')


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


def _tokens(*, member: bool = True, jwt: _Jwt | None = None, users: _Users | None = None) -> CLITokens:
    return CLITokens(
        team_db=_Team(member=member),  # type: ignore[arg-type]
        org_db=_Orgs(),  # type: ignore[arg-type]
        groups=_Groups(),  # type: ignore[arg-type]
        jwt=jwt or _Jwt(),  # type: ignore[arg-type]
        handoff=_Handoff(),  # type: ignore[arg-type]
        subjects=_Subjects(),  # type: ignore[arg-type]
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
