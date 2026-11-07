# ruff: noqa: E501
# E501 only: HTML string literals are long by nature, and all 51
# warnings here are inside markup. The directive above used to carry
# no codes at all, which switched off every rule for the whole file —
# unused imports, undefined names and the rest — to silence those 51.
"""Email templates for igni platform — styled to match the landing page."""

import html

from pydantic import BaseModel

from app.config import settings

# ---------------------------------------------------------------------------
# Brand constants (matching the public site's landing page)
# ---------------------------------------------------------------------------
_ACCENT = '#F97316'
_ACCENT_RED = '#DC2626'
_GRADIENT = 'linear-gradient(135deg, #F97316, #DC2626)'
_BG_DEFAULT = '#ffffff'
_BG_MUTED = '#f6f8fa'
_BG_INSET = '#f0f3f6'
_FG_DEFAULT = '#1f2328'
_FG_MUTED = '#656d76'
_BORDER = '#d1d9e0'
_SUCCESS = '#1a7f37'
_DANGER = '#cf222e'
_WARNING = '#d29922'
_FONT_STACK = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
_MONO_STACK = "ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace"

# igni flame SVG (matches EmberLogo.tsx)
_LOGO_SVG = """<svg width="32" height="32" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M7.998 14.5c-3.312 0-6-2.246-6-5.014 0-3.047 2.262-5.674 3.625-7.018.27-.266.717-.058.717.318 0 1.335.877 2.562 1.753 2.562.645 0 .972-.379 1.096-.65.258-.564.2-1.363.027-2.043-.098-.387.3-.726.65-.498C10.87 3.534 14 6.186 14 9.486c0 2.768-2.688 5.014-6.002 5.014z" fill="#f85149"/>
  <path d="M8.7 13.563c-1.822 0-3.3-1.235-3.3-2.758 0-1.677 1.244-3.122 1.994-3.862.149-.146.394-.032.394.175 0 .734.482 1.41.964 1.41.355 0 .534-.209.603-.358.142-.31.11-.75.015-1.124-.054-.213.165-.4.357-.274 1.266.894 2.987 2.354 2.987 4.033 0 1.523-1.479 2.758-3.014 2.758z" fill="#f0883e"/>
</svg>"""


# ---------------------------------------------------------------------------
# Shared skeleton
# ---------------------------------------------------------------------------


class EmailContent(BaseModel):
    """A rendered email: what it says, and the body that says it.

    Every template returned a bare `tuple[str, str]`, and every caller
    unpacked it as `subject, html_body = ...`. Two strings in a fixed
    order, crossing a module boundary, distinguishable only by
    position — swap them and nothing complains until somebody reads
    an email whose subject is a page of HTML.
    """

    subject: str
    html_body: str


def _esc(value: object) -> str:
    """Caller text, safe to drop into HTML.

    The `_p`, `_info_box` and `_error_box` helpers take **markup** —
    their callers pass `<strong>` deliberately — so escaping cannot
    live in them. It has to happen where caller data enters, which is
    here, named, at the top of each template.

    This matters most for names and organisation titles: they are
    written by whoever signs up and land in an email sent to somebody
    else. A name like `<b>urgent</b>` would render as bold text in
    other people's inboxes, and an unbalanced `<` would break the
    layout from that point down.

    `html.escape` with `quote=True`, because several of these values
    are interpolated inside attribute positions in `_wrap`.
    """
    return html.escape(str(value), quote=True)


def _wrap(title: str, subtitle: str, content_html: str, email: str) -> str:
    """Return a full HTML email wrapped in the landing-page-style layout."""
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
</head>
<body style="margin: 0; padding: 0; font-family: {_FONT_STACK}; background-color: {_BG_MUTED};">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: {_BG_MUTED}; padding: 48px 20px;">
        <tr>
            <td align="center">
                <table width="600" cellpadding="0" cellspacing="0" border="0" style="background-color: {_BG_DEFAULT}; border-radius: 12px; border: 1px solid {_BORDER}; overflow: hidden;">

                    <!-- Header -->
                    <tr>
                        <td style="padding: 40px 40px 32px; text-align: center;">
                            <div style="margin-bottom: 20px;">{_LOGO_SVG}</div>
                            <h1 style="margin: 0 0 8px; font-size: 28px; font-weight: 800; letter-spacing: -0.03em; color: {_FG_DEFAULT};">
                                {title}
                            </h1>
                            <p style="margin: 0; font-size: 14px; font-weight: 500; color: {_FG_MUTED};">
                                {subtitle}
                            </p>
                        </td>
                    </tr>

                    <!-- Accent line -->
                    <tr>
                        <td style="padding: 0 40px;">
                            <div style="height: 2px; background: {_GRADIENT}; border-radius: 2px;"></div>
                        </td>
                    </tr>

                    <!-- Content -->
                    <tr>
                        <td style="padding: 32px 40px 40px;">
{content_html}
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: {_BG_MUTED}; padding: 24px 40px; text-align: center; border-top: 1px solid {_BORDER};">
                            <p style="margin: 0 0 8px; color: {_FG_MUTED}; font-size: 13px;">
                                This email was sent to {email}
                            </p>
                            <p style="margin: 0; color: {_FG_MUTED}; font-size: 12px;">
                                &copy; 2026 ignite-ember.sh &mdash; where code sparks itself.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Reusable blocks (matching landing page components)
# ---------------------------------------------------------------------------


def _otp_block(code: str) -> str:
    """OTP code display — card style matching landing-feature-card."""
    return f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px;">
                                <tr>
                                    <td align="center" style="background-color: {_BG_MUTED}; border: 1px solid {_BORDER}; border-radius: 12px; padding: 32px;">
                                        <div style="font-size: 42px; font-weight: 700; letter-spacing: 8px; background: {_GRADIENT}; -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; font-family: {_MONO_STACK};">
                                            {code}
                                        </div>
                                    </td>
                                </tr>
                            </table>"""


def _cta_button(text: str, url: str) -> str:
    """CTA button — matches landing-btn-primary."""
    return f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px;">
                                <tr>
                                    <td align="center">
                                        <a href="{url}" style="display: inline-block; background: {_GRADIENT}; color: #ffffff; text-decoration: none; padding: 14px 40px; border-radius: 6px; font-size: 16px; font-weight: 600;">
                                            {text}
                                        </a>
                                    </td>
                                </tr>
                            </table>"""


def _info_box(text: str) -> str:
    """Info callout — matches landing-feature-icon gradient background."""
    return f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 24px 0;">
                                <tr>
                                    <td style="background: linear-gradient(135deg, rgba(249, 115, 22, 0.08), rgba(220, 38, 38, 0.04)); border-left: 3px solid {_ACCENT}; padding: 16px 20px; border-radius: 0 8px 8px 0;">
                                        <p style="margin: 0; color: {_FG_DEFAULT}; font-size: 14px; line-height: 1.6;">
                                            {text}
                                        </p>
                                    </td>
                                </tr>
                            </table>"""


def _warning_box(text: str) -> str:
    """Warning callout — yellow accent."""
    return f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 24px 0;">
                                <tr>
                                    <td style="background-color: #fefce8; border-left: 3px solid {_WARNING}; padding: 16px 20px; border-radius: 0 8px 8px 0;">
                                        <p style="margin: 0; color: {_FG_DEFAULT}; font-size: 14px; line-height: 1.6;">
                                            {text}
                                        </p>
                                    </td>
                                </tr>
                            </table>"""


def _error_box(text: str) -> str:
    """Error callout — red accent."""
    return f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 24px 0;">
                                <tr>
                                    <td style="background-color: #fef2f2; border-left: 3px solid {_DANGER}; padding: 16px 20px; border-radius: 0 8px 8px 0;">
                                        <p style="margin: 0; color: {_FG_DEFAULT}; font-size: 14px; line-height: 1.6;">
                                            {text}
                                        </p>
                                    </td>
                                </tr>
                            </table>"""


def _section_label(text: str) -> str:
    """Section label — matches landing-section-label."""
    return f'                            <p style="margin: 0 0 8px; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: {_ACCENT};">{text}</p>'


def _greeting(name: str | None = None) -> str:
    """Return 'Hello Name,' or 'Hello,' if no name provided."""
    if name:
        return _p(f'Hello {name},')
    return _p('Hello,')


def _p(text: str, *, margin: str = '0 0 16px', muted: bool = False) -> str:
    color = _FG_MUTED if muted else _FG_DEFAULT
    size = '13px' if muted else '15px'
    return f'                            <p style="margin: {margin}; color: {color}; font-size: {size}; line-height: 1.6;">{text}</p>'


# ---------------------------------------------------------------------------
# 1. Sign-up verification
# ---------------------------------------------------------------------------


def get_sign_up_email_template(*, code: str, email: str, name: str | None = None) -> EmailContent:
    """
    Generate OTP verification email template for sign-up.

    Args:
        code: The 6-digit OTP code
        email: The recipient email address
        name: Optional recipient name

    Returns:
        The rendered subject and HTML body.
    """
    # Escaped where caller text enters. The block helpers below
    # take markup on purpose, so this cannot live in them.
    code = _esc(code)
    email = _esc(email)
    name = _esc(name) if name else name
    subject = 'Verify Your Email \u2014 ignite-ember.sh'

    content = '\n'.join(
        [
            _section_label('Account Verification'),
            _greeting(name),
            _p(
                'Thanks for signing up to <strong>ignite-ember.sh</strong>. Use the verification code below to activate your account:',
                margin='0 0 24px',
            ),
            _otp_block(code),
            _p('This code will expire in <strong>10 minutes</strong>.'),
            _warning_box(
                "<strong>Security Notice:</strong> If you didn't sign up for ignite-ember.sh, please ignore this email."
            ),
            _p(
                'For your security, never share this code with anyone. Our team will never ask you for it.',
                margin='0',
                muted=True,
            ),
        ]
    )

    html_body = _wrap('Verify Your Email', 'ignite-ember.sh', content, email)
    return EmailContent(subject=subject, html_body=html_body)


# ---------------------------------------------------------------------------
# 2. OTP sign-in
# ---------------------------------------------------------------------------


def get_otp_email_template(*, code: str, email: str, name: str | None = None) -> EmailContent:
    """
    Generate OTP sign-in email template.

    Args:
        code: The 6-digit OTP code
        email: The recipient email address
        name: Optional recipient name

    Returns:
        The rendered subject and HTML body.
    """
    # Escaped where caller text enters. The block helpers below
    # take markup on purpose, so this cannot live in them.
    code = _esc(code)
    email = _esc(email)
    name = _esc(name) if name else name
    subject = 'Your Sign-In Code'

    content = '\n'.join(
        [
            _section_label('Sign In'),
            _greeting(name),
            _p(
                'You requested a sign-in code for your account. Use the code below to complete your sign-in:',
                margin='0 0 24px',
            ),
            _otp_block(code),
            _p('This code will expire in <strong>10 minutes</strong>.'),
            _warning_box(
                "<strong>Security Notice:</strong> If you didn't request this code, please ignore this email or contact support if you have concerns about your account security."
            ),
            _p(
                'For your security, never share this code with anyone. Our team will never ask you for this code.',
                margin='0',
                muted=True,
            ),
        ]
    )

    html_body = _wrap('Sign-In Code', 'ignite-ember.sh', content, email)
    return EmailContent(subject=subject, html_body=html_body)


# ---------------------------------------------------------------------------
# 3. Team invitation
# ---------------------------------------------------------------------------


def get_team_invite_email_template(
    *, email: str, role: str, invited_by: str | None = None, name: str | None = None
) -> EmailContent:
    """
    Generate team invitation email template.

    Args:
        email: The recipient email address
        role: The role being assigned (admin or member)
        invited_by: Name or email of the person who sent the invite
        name: Optional recipient name

    Returns:
        The rendered subject and HTML body.
    """
    # Escaped where caller text enters. The block helpers below
    # take markup on purpose, so this cannot live in them.
    email = _esc(email)
    role = _esc(role)
    invited_by = _esc(invited_by) if invited_by else invited_by
    name = _esc(name) if name else name
    subject = "You've Been Invited to igni \u2014 ignite-ember.sh"

    invited_line = f'<strong>{invited_by}</strong> has invited you' if invited_by else 'You have been invited'
    role_label = role.capitalize()

    content = '\n'.join(
        [
            _section_label('Team Invitation'),
            _greeting(name),
            _p(
                f'{invited_line} to join the team on <strong>ignite-ember.sh</strong> as a <strong>{role_label}</strong>.',
                margin='0 0 24px',
            ),
            _cta_button('Accept Invitation', f'{settings.portal_base_url}/signup?email={email}'),
            _p(f'Sign in with <strong>{email}</strong> to get started.'),
            _info_box(
                f'<strong>Your role: {role_label}</strong> \u2014 you can start using igni with your team right away.'
            ),
            _p("If you weren't expecting this invitation, you can safely ignore this email.", margin='0', muted=True),
        ]
    )

    html_body = _wrap("You're Invited", 'ignite-ember.sh', content, email)
    return EmailContent(subject=subject, html_body=html_body)


# ---------------------------------------------------------------------------
# 4. Password reset
# ---------------------------------------------------------------------------


def get_password_reset_email_template(*, code: str, email: str, name: str | None = None) -> EmailContent:
    """
    Generate password reset email template.

    Args:
        code: The 6-digit verification code
        email: The recipient email address
        name: Optional recipient name

    Returns:
        The rendered subject and HTML body.
    """
    # Escaped where caller text enters. The block helpers below
    # take markup on purpose, so this cannot live in them.
    code = _esc(code)
    email = _esc(email)
    name = _esc(name) if name else name
    subject = 'Password Reset Code'

    tips_section = f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 24px 0;">
                                <tr>
                                    <td style="background: linear-gradient(135deg, rgba(249, 115, 22, 0.08), rgba(220, 38, 38, 0.04)); border-left: 3px solid {_ACCENT}; padding: 16px 20px; border-radius: 0 8px 8px 0;">
                                        <p style="margin: 0 0 10px; color: {_FG_DEFAULT}; font-size: 14px; font-weight: 600;">Tips for a Strong Password</p>
                                        <ul style="margin: 0; padding-left: 20px; color: {_FG_DEFAULT}; font-size: 14px; line-height: 1.8;">
                                            <li>Use at least 8 characters</li>
                                            <li>Include uppercase and lowercase letters</li>
                                            <li>Add numbers and special characters</li>
                                            <li>Avoid common words or personal information</li>
                                            <li>Don't reuse passwords from other accounts</li>
                                        </ul>
                                    </td>
                                </tr>
                            </table>"""

    content = '\n'.join(
        [
            _section_label('Password Reset'),
            _greeting(name),
            _p('You requested to reset your password. Use the verification code below to proceed:', margin='0 0 24px'),
            _otp_block(code),
            _p('This code will expire in <strong>10 minutes</strong>.'),
            _p("After verifying this code, you'll be able to set a new password for your account.", margin='0 0 24px'),
            _error_box(
                "<strong>Important:</strong> If you didn't request a password reset, please contact our support team immediately. Someone may be trying to access your account."
            ),
            tips_section,
            _p(
                'For your security, never share this code with anyone, including our support team. We will never ask for your verification code.',
                margin='0',
                muted=True,
            ),
        ]
    )

    html_body = _wrap('Password Reset', 'ignite-ember.sh', content, email)
    return EmailContent(subject=subject, html_body=html_body)


# ---------------------------------------------------------------------------
# 5. Welcome email (post-registration)
# ---------------------------------------------------------------------------


def get_welcome_email_template(*, email: str, name: str | None = None) -> EmailContent:
    """
    Generate welcome email sent after account creation.

    Args:
        email: The recipient email address
        name: Optional recipient name

    Returns:
        The rendered subject and HTML body.
    """
    # Escaped where caller text enters. The block helpers below
    # take markup on purpose, so this cannot live in them.
    email = _esc(email)
    name = _esc(name) if name else name
    subject = 'Welcome to igni \u2014 ignite-ember.sh'

    content = '\n'.join(
        [
            _section_label('Public Beta'),
            _greeting(name),
            _p(
                'Welcome to the <strong>igni</strong> public beta \u2014 where code sparks itself.',
                margin='0 0 24px',
            ),
            _info_box(
                '<strong>You\u2019re on the Pro plan \u2014 free during the beta.</strong>'
                ' You get up to 5 parallel requests powered by MiniMax M2.5, with no usage limits.'
                ' When the beta ends, we\u2019ll upgrade you to the latest model available and you can'
                ' continue on a Lite, Pro, or Max plan \u2014 each with unlimited usage but different speed and concurrency tiers.'
            ),
            _p('Get started by installing igni:', margin='0 0 8px'),
            f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 8px;">
                                <tr>
                                    <td style="background-color: {_BG_MUTED}; border: 1px solid {_BORDER}; border-radius: 12px; padding: 16px 24px; font-family: {_MONO_STACK}; font-size: 14px; color: {_FG_DEFAULT};">
                                        <span style="color: {_FG_MUTED};">$</span> brew install ignite-ember/tap/ignite-ember
                                    </td>
                                </tr>
                            </table>""",
            _p('or', margin='0 0 8px', muted=True),
            f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px;">
                                <tr>
                                    <td style="background-color: {_BG_MUTED}; border: 1px solid {_BORDER}; border-radius: 12px; padding: 16px 24px; font-family: {_MONO_STACK}; font-size: 14px; color: {_FG_DEFAULT};">
                                        <span style="color: {_FG_MUTED};">$</span> pip install igni-core
                                    </td>
                                </tr>
                            </table>""",
            _p("If you have any questions, don't hesitate to reach out to our support team.", margin='0', muted=True),
        ]
    )

    html_body = _wrap('Welcome to igni', 'ignite-ember.sh', content, email)
    return EmailContent(subject=subject, html_body=html_body)
