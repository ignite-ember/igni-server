"""The email the auth endpoints send: verification codes and the welcome."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import BackgroundTasks

from app.services.auth.auth_service import AuthService
from app.services.auth.otp_service import CodeSendThrottled, OTPResult, OTPService
from app.services.email.delivery import EmailDelivery
from app.services.email.service import EmailService

#: Generates and stores a code for an address.
CodeIssuer = Callable[[str], Awaitable[OTPResult]]


class CodeMailer(Protocol):
    """Sends one code to one address; an `EmailService` template method."""

    def __call__(self, *, to_email: str, code: str) -> Awaitable[bool]: ...


class AuthEmails:
    """Issues verification codes and sends what the auth flows email."""

    def __init__(
        self,
        *,
        delivery: EmailDelivery | None = None,
        emails: EmailService | None = None,
        otp: OTPService | None = None,
    ) -> None:
        self._delivery = delivery or EmailDelivery()
        self._emails = emails or EmailService()
        self._otp = otp or OTPService()

    async def send_sign_up_code(self, bg: BackgroundTasks, email: str) -> str:
        return await self._send_code(
            bg,
            issue=AuthService.send_sign_up_code,
            mail=self._emails.send_sign_up_code,
            purpose='sign-up',
            email=email,
        )

    async def send_sign_in_code(self, bg: BackgroundTasks, email: str) -> str:
        return await self._send_code(
            bg,
            issue=self._otp.generate_signin_code,
            mail=self._emails.send_otp_code,
            purpose='sign-in',
            email=email,
        )

    async def send_reset_password_code(self, bg: BackgroundTasks, email: str) -> str:
        return await self._send_code(
            bg,
            issue=self._otp.generate_password_reset_code,
            mail=self._emails.send_password_reset_code,
            purpose='password-reset',
            email=email,
        )

    def welcome(self, bg: BackgroundTasks, *, email: str, name: str | None = None) -> None:
        if self._delivery.enabled:
            bg.add_task(self._emails.send_welcome, to_email=email, name=name)
        else:
            # Informational; nothing is lost that has to be replaced by hand.
            self._delivery.note_undeliverable(purpose='welcome', email=email)

    async def _send_code(
        self,
        bg: BackgroundTasks,
        *,
        issue: CodeIssuer,
        mail: CodeMailer,
        purpose: str,
        email: str,
    ) -> str:
        """Issue a verification code and answer identically however it went.

        The three code endpoints do the same four things in the same
        order, and F112 found two ways that mattered to get wrong:

        * a **throttled** send must not be distinguishable from a delivered
          one. A 429 would say "somebody asked for a code for this address
          recently", which is an activity oracle on endpoints designed to
          reveal nothing about an address at all;
        * a **suppressed** send — the address already has an account, on the
          sign-up path — must not be distinguishable either. That difference
          was the enumeration oracle: `400 User already registered` against
          `200`.

        So there is one exit for all four outcomes: sent, throttled,
        suppressed, and no relay configured. The last one still differs in
        wording, because a deployment with no mail relay tells *everybody*
        the same thing and that is a property of the deployment rather than
        of the address.
        """
        try:
            result = await issue(email)
        except CodeSendThrottled:
            # Silent, and identical — including on a deployment with no mail
            # relay, which is the case the first version got wrong. It
            # returned "has been sent" while a *delivered* code on the same
            # deployment returns `no_relay_message()`, so the throttle was
            # distinguishable on exactly the air-gapped installs where codes
            # go to the log. Caught by the test asserting the two are
            # identical.
            #
            # The counter and the log line in code_throttle.py are how an
            # operator sees a refusal.
            if not self._delivery.enabled:
                return self._delivery.no_relay_message()
            return f'Verification code has been sent to {email}'

        if not self._delivery.enabled:
            # No relay. The code goes to the log only if the operator turned
            # LOG_SIGN_IN_CODES on (F35) — and never into the response, which
            # any anonymous caller could request for any address.
            if not result.suppress_delivery:
                self._delivery.record_undeliverable_code(purpose=purpose, email=result.email, code=result.code)
            return self._delivery.no_relay_message()

        if not result.suppress_delivery:
            bg.add_task(mail, to_email=result.email, code=result.code)

        return result.message


def auth_emails() -> AuthEmails:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return AuthEmails()
