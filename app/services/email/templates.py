# ruff: noqa: E501
# E501 only: the copy below is long sentences of markup.
"""What each of igni's emails says.

The layout and blocks are `EmailMarkup`'s; this module only composes
them. Caller text passed to a block that takes text is escaped by the
block. Caller text interpolated into markup here — an inviter's name, a
role — goes through `EmailMarkup.escape` where it enters.
"""

from pydantic import BaseModel

from app.services.email.markup import EmailMarkup

_BRAND = 'ignite-ember.sh'

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
    """Renders each outbound email to a subject and an HTML body."""

    def __init__(self, *, markup: EmailMarkup | None = None) -> None:
        self._markup = markup or EmailMarkup()

    def sign_up(self, *, code: str, email: str, name: str | None = None) -> EmailContent:
        """The verification code that activates a new account."""
        m = self._markup
        content = self._join(
            m.section_label('Account Verification'),
            m.greeting(name),
            m.paragraph(
                'Thanks for signing up to <strong>ignite-ember.sh</strong>. Use the verification code below to activate your account:',
                margin='0 0 24px',
            ),
            m.otp(code),
            m.paragraph('This code will expire in <strong>10 minutes</strong>.'),
            m.callout(
                "<strong>Security Notice:</strong> If you didn't sign up for ignite-ember.sh, please ignore this email.",
                style=m.WARNING,
            ),
            m.paragraph(
                'For your security, never share this code with anyone. Our team will never ask you for it.',
                margin='0',
                muted=True,
            ),
        )
        return self._render(
            subject='Verify Your Email — ignite-ember.sh', title='Verify Your Email', content=content, email=email
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
                "<strong>Security Notice:</strong> If you didn't request this code, please ignore this email or contact support if you have concerns about your account security.",
                style=m.WARNING,
            ),
            m.paragraph(
                'For your security, never share this code with anyone. Our team will never ask you for this code.',
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
                f'{invited_line} to join the team on <strong>ignite-ember.sh</strong> as a <strong>{role_label}</strong>.',
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
            subject="You've Been Invited to igni — ignite-ember.sh",
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
                "<strong>Important:</strong> If you didn't request a password reset, please contact our support team immediately. Someone may be trying to access your account.",
                style=m.ERROR,
            ),
            m.callout_list(heading='Tips for a Strong Password', items=_PASSWORD_TIPS, style=m.INFO),
            m.paragraph(
                'For your security, never share this code with anyone, including our support team. We will never ask for your verification code.',
                margin='0',
                muted=True,
            ),
        )
        return self._render(subject='Password Reset Code', title='Password Reset', content=content, email=email)

    def welcome(self, *, email: str, name: str | None = None) -> EmailContent:
        """Sent once an account exists: what the beta includes and how to install."""
        m = self._markup
        content = self._join(
            m.section_label('Public Beta'),
            m.greeting(name),
            m.paragraph(
                'Welcome to the <strong>igni</strong> public beta — where code sparks itself.', margin='0 0 24px'
            ),
            m.callout(
                '<strong>You’re on the Pro plan — free during the beta.</strong>'
                ' You get up to 5 parallel requests powered by MiniMax M2.5, with no usage limits.'
                ' When the beta ends, we’ll upgrade you to the latest model available and you can'
                ' continue on a Lite, Pro, or Max plan — each with unlimited usage but different speed and concurrency tiers.',
                style=m.INFO,
            ),
            m.paragraph('Get started by installing igni:', margin='0 0 8px'),
            m.command('brew install ignite-ember/tap/ignite-ember', margin='0 0 8px'),
            m.paragraph('or', margin='0 0 8px', muted=True),
            m.command('pip install igni-core', margin='0 0 24px'),
            m.paragraph(
                "If you have any questions, don't hesitate to reach out to our support team.", margin='0', muted=True
            ),
        )
        return self._render(
            subject='Welcome to igni — ignite-ember.sh', title='Welcome to igni', content=content, email=email
        )

    def _render(self, *, subject: str, title: str, content: str, email: str) -> EmailContent:
        html_body = self._markup.page(title=title, subtitle=_BRAND, content=content, email=email)
        return EmailContent(subject=subject, html_body=html_body)

    @staticmethod
    def _join(*blocks: str) -> str:
        return '\n'.join(blocks)
