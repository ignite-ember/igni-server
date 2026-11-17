# ruff: noqa: E501
# E501 only: the copy below is long sentences of markup.
"""What each of igni's emails says.

The layout and blocks are `EmailMarkup`'s; this module only composes
them. Caller text passed to a block that takes text is escaped by the
block. Caller text interpolated into markup here — an inviter's name, a
role — goes through `EmailMarkup.escape` where it enters.
"""

from pydantic import BaseModel

from app.config import settings
from app.services.email.markup import EmailMarkup

_PASSWORD_TIPS = (
    'Use at least 8 characters',
    'Include uppercase and lowercase letters',
    'Add numbers and special characters',
    'Avoid common words or personal information',
    "Don't reuse passwords from other accounts",
)


class EmailContent(BaseModel):
    """A rendered email: what it says, and the body that says it."""

    subject: str
    html_body: str


class EmailTemplates:
    """Renders each outbound email to a subject and an HTML body.

    `brand` is who the mail says it is from — in the subject, the header
    and the footer. It is `EMAIL_FROM_NAME` unless given. igni-server is
    only ever run by the customer, so the vendor never signs: an empty
    brand (the default) means the mail names no sender at all — no subject
    suffix, no header subtitle, no footer line, and sentences that would
    have named one read without it (`docs/self-hosting.md` §6).
    """

    def __init__(
        self,
        *,
        markup: EmailMarkup | None = None,
        brand: str | None = None,
        portal_url: str | None = None,
        client_install_url: str | None = None,
    ) -> None:
        self._markup = markup or EmailMarkup()
        self._brand = brand if brand is not None else settings.email_from_name
        self._portal_url = portal_url if portal_url is not None else settings.portal_base_url
        self._client_install_url = client_install_url if client_install_url is not None else settings.client_install_url

    @property
    def brand(self) -> str:
        return self._brand

    @property
    def portal_url(self) -> str:
        """This deployment's portal, where its people sign in; empty if unset."""
        return self._portal_url.rstrip('/')

    @property
    def client_install_url(self) -> str:
        """The operator's own page for getting the igni app; empty if unset."""
        return self._client_install_url

    def sign_up(self, *, code: str, email: str, name: str | None = None) -> EmailContent:
        """The verification code that activates a new account."""
        m = self._markup
        content = self._join(
            m.section_label('Account Verification'),
            m.greeting(name),
            m.paragraph(
                f'Thanks for signing up{self._to_brand()}. Use the verification code below to activate your account:',
                margin='0 0 24px',
            ),
            m.otp(code),
            m.paragraph('This code will expire in <strong>10 minutes</strong>.'),
            m.callout(
                f"<strong>Security Notice:</strong> If you didn't sign up{self._for_brand()}, please ignore this email.",
                style=m.WARNING,
            ),
            m.paragraph(
                'For your security, never share this code with anyone. Nobody from your organisation will ask you for it.',
                margin='0',
                muted=True,
            ),
        )
        return self._render(
            subject=self._subject('Verify Your Email'), title='Verify Your Email', content=content, email=email
        )

    def sign_in(self, *, code: str, email: str, name: str | None = None) -> EmailContent:
        """A one-time sign-in code."""
        m = self._markup
        content = self._join(
            m.section_label('Sign In'),
            m.greeting(name),
            m.paragraph(
                'You requested a sign-in code for your account. Use the code below to complete your sign-in:',
                margin='0 0 24px',
            ),
            m.otp(code),
            m.paragraph('This code will expire in <strong>10 minutes</strong>.'),
            m.callout(
                "<strong>Security Notice:</strong> If you didn't request this code, please ignore this email or contact your administrator if you have concerns about your account security.",
                style=m.WARNING,
            ),
            m.paragraph(
                'For your security, never share this code with anyone. Nobody from your organisation will ask you for it.',
                margin='0',
                muted=True,
            ),
        )
        return self._render(subject='Your Sign-In Code', title='Sign-In Code', content=content, email=email)

    def team_invite(
        self,
        *,
        email: str,
        role: str,
        invite_url: str,
        invited_by: str | None = None,
        name: str | None = None,
    ) -> EmailContent:
        """An invitation to join an organisation as `role`, claimed at `invite_url`."""
        m = self._markup
        role_label = m.escape(role.capitalize())
        invited_line = (
            f'<strong>{m.escape(invited_by)}</strong> has invited you' if invited_by else 'You have been invited'
        )
        content = self._join(
            m.section_label('Team Invitation'),
            m.greeting(name),
            m.paragraph(
                f'{invited_line} to join {self._team()} on <strong>igni</strong> as a <strong>{role_label}</strong>.',
                margin='0 0 24px',
            ),
            m.cta_button(text='Accept Invitation', url=invite_url),
            m.paragraph(f'Sign in with <strong>{m.escape(email)}</strong> to get started.'),
            m.callout(
                f'<strong>Your role: {role_label}</strong> — you can start using igni with your team right away.',
                style=m.INFO,
            ),
            m.paragraph(
                "If you weren't expecting this invitation, you can safely ignore this email.", margin='0', muted=True
            ),
        )
        return self._render(
            subject=self._subject("You've Been Invited to igni"),
            title="You're Invited",
            content=content,
            email=email,
        )

    def password_reset(self, *, code: str, email: str, name: str | None = None) -> EmailContent:
        """The verification code that lets somebody set a new password."""
        m = self._markup
        content = self._join(
            m.section_label('Password Reset'),
            m.greeting(name),
            m.paragraph(
                'You requested to reset your password. Use the verification code below to proceed:', margin='0 0 24px'
            ),
            m.otp(code),
            m.paragraph('This code will expire in <strong>10 minutes</strong>.'),
            m.paragraph(
                "After verifying this code, you'll be able to set a new password for your account.", margin='0 0 24px'
            ),
            m.callout(
                "<strong>Important:</strong> If you didn't request a password reset, please contact your administrator immediately. Someone may be trying to access your account.",
                style=m.ERROR,
            ),
            m.callout_list(heading='Tips for a Strong Password', items=_PASSWORD_TIPS, style=m.INFO),
            m.paragraph(
                'For your security, never share this code with anyone, including your administrator. Nobody from your organisation will ask you for it.',
                margin='0',
                muted=True,
            ),
        )
        return self._render(subject='Password Reset Code', title='Password Reset', content=content, email=email)

    def welcome(self, *, email: str, name: str | None = None) -> EmailContent:
        """Sent once an account exists: where to sign in, and how to get the app.

        igni runs on isolated networks, so this names no public install
        command or URL: only this deployment's portal and, when the
        operator configured one, their own page for getting the app
        (`CLIENT_INSTALL_URL`). Without one, the administrator is who to ask.
        """
        m = self._markup
        portal = self.portal_url
        sign_in = f'Sign in at {m.link(portal)}' if portal else 'Ask your administrator where to sign in.'
        get_the_app = (
            m.paragraph(f'Get the igni app: {m.link(self._client_install_url)}', margin='0 0 24px')
            if self._client_install_url
            else m.paragraph('Ask your administrator how to get the igni app.', margin='0 0 24px')
        )
        content = self._join(
            m.section_label('Welcome'),
            m.greeting(name),
            m.paragraph(
                f'Your <strong>igni</strong> account{self._with_brand()} is ready. {sign_in}',
                margin='0 0 24px',
            ),
            get_the_app,
            m.paragraph('If you have any questions, ask your administrator.', margin='0', muted=True),
        )
        return self._render(
            subject=self._subject('Welcome to igni'), title='Welcome to igni', content=content, email=email
        )

    def _subject(self, subject: str) -> str:
        """`subject — brand`, or the subject alone when there is no brand."""
        return f'{subject} — {self._brand}' if self._brand else subject

    def _to_brand(self) -> str:
        """' to <brand>' for 'Thanks for signing up', or nothing."""
        return f' to <strong>{self._markup.escape(self._brand)}</strong>' if self._brand else ''

    def _for_brand(self) -> str:
        """' for <brand>' for "If you didn't sign up", or nothing."""
        return f' for {self._markup.escape(self._brand)}' if self._brand else ''

    def _with_brand(self) -> str:
        """' with <brand>' for 'Your igni account … is ready', or nothing."""
        return f' with <strong>{self._markup.escape(self._brand)}</strong>' if self._brand else ''

    def _team(self) -> str:
        """Who the invitee joins: the named team, or their organisation's."""
        return f'<strong>{self._markup.escape(self._brand)}</strong>' if self._brand else "your organisation's team"

    def _render(self, *, subject: str, title: str, content: str, email: str) -> EmailContent:
        brand = self._markup.escape(self._brand)
        html_body = self._markup.page(title=title, subtitle=brand, content=content, email=email, footer=brand)
        return EmailContent(subject=subject, html_body=html_body)

    @staticmethod
    def _join(*blocks: str) -> str:
        return '\n'.join(blocks)
