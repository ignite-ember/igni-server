# ruff: noqa: E501
# E501 only: HTML string literals are long by nature, and every
# warning here is inside markup.
"""The building blocks igni's emails are made of, styled like the landing page.

`EmailMarkup` owns the brand and the blocks; what each email *says*
lives in `templates.py`.

One rule decides where escaping happens. A block that takes **text** —
a code, a name, an address, a shell command — escapes it, because that
text is never markup and often comes from whoever signed up. A block
that takes **markup** (`paragraph`, the callouts) does not, because its
callers pass `<strong>` on purpose; caller text interpolated into such
markup is escaped by the template, with `escape`, where it enters.
"""

import html

from pydantic import BaseModel, ConfigDict


class CalloutStyle(BaseModel):
    """How one kind of callout is coloured."""

    model_config = ConfigDict(frozen=True)

    background: str
    border: str


class EmailMarkup:
    """Renders the layout and reusable blocks shared by every email."""

    ACCENT = '#F97316'
    GRADIENT = 'linear-gradient(135deg, #F97316, #DC2626)'
    BG_DEFAULT = '#ffffff'
    BG_MUTED = '#f6f8fa'
    FG_DEFAULT = '#1f2328'
    FG_MUTED = '#656d76'
    BORDER = '#d1d9e0'
    FONT_STACK = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    MONO_STACK = "ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace"

    INFO = CalloutStyle(
        background='linear-gradient(135deg, rgba(249, 115, 22, 0.08), rgba(220, 38, 38, 0.04))', border='#F97316'
    )
    WARNING = CalloutStyle(background='#fefce8', border='#d29922')
    ERROR = CalloutStyle(background='#fef2f2', border='#cf222e')

    # igni flame SVG (matches EmberLogo.tsx)
    LOGO_SVG = """<svg width="32" height="32" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M7.998 14.5c-3.312 0-6-2.246-6-5.014 0-3.047 2.262-5.674 3.625-7.018.27-.266.717-.058.717.318 0 1.335.877 2.562 1.753 2.562.645 0 .972-.379 1.096-.65.258-.564.2-1.363.027-2.043-.098-.387.3-.726.65-.498C10.87 3.534 14 6.186 14 9.486c0 2.768-2.688 5.014-6.002 5.014z" fill="#f85149"/>
  <path d="M8.7 13.563c-1.822 0-3.3-1.235-3.3-2.758 0-1.677 1.244-3.122 1.994-3.862.149-.146.394-.032.394.175 0 .734.482 1.41.964 1.41.355 0 .534-.209.603-.358.142-.31.11-.75.015-1.124-.054-.213.165-.4.357-.274 1.266.894 2.987 2.354 2.987 4.033 0 1.523-1.479 2.758-3.014 2.758z" fill="#f0883e"/>
</svg>"""

    def escape(self, value: object) -> str:
        """Caller text, safe to drop into HTML.

        This matters most for names and organisation titles: they are
        written by whoever signs up and land in an email sent to somebody
        else. A name like `<b>urgent</b>` would render as bold text in
        other people's inboxes, and an unbalanced `<` would break the
        layout from that point down.

        `quote=True`, because some of these values land inside attributes.
        """
        return html.escape(str(value), quote=True)

    def page(self, *, title: str, subtitle: str, content: str, email: str) -> str:
        """A full HTML email in the landing-page layout.

        `title`, `subtitle` and `content` are markup; `email` is the
        recipient's address and is escaped here.
        """
        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
</head>
<body style="margin: 0; padding: 0; font-family: {self.FONT_STACK}; background-color: {self.BG_MUTED};">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: {self.BG_MUTED}; padding: 48px 20px;">
        <tr>
            <td align="center">
                <table width="600" cellpadding="0" cellspacing="0" border="0" style="background-color: {self.BG_DEFAULT}; border-radius: 12px; border: 1px solid {self.BORDER}; overflow: hidden;">

                    <!-- Header -->
                    <tr>
                        <td style="padding: 40px 40px 32px; text-align: center;">
                            <div style="margin-bottom: 20px;">{self.LOGO_SVG}</div>
                            <h1 style="margin: 0 0 8px; font-size: 28px; font-weight: 800; letter-spacing: -0.03em; color: {self.FG_DEFAULT};">
                                {title}
                            </h1>
                            <p style="margin: 0; font-size: 14px; font-weight: 500; color: {self.FG_MUTED};">
                                {subtitle}
                            </p>
                        </td>
                    </tr>

                    <!-- Accent line -->
                    <tr>
                        <td style="padding: 0 40px;">
                            <div style="height: 2px; background: {self.GRADIENT}; border-radius: 2px;"></div>
                        </td>
                    </tr>

                    <!-- Content -->
                    <tr>
                        <td style="padding: 32px 40px 40px;">
{content}
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: {self.BG_MUTED}; padding: 24px 40px; text-align: center; border-top: 1px solid {self.BORDER};">
                            <p style="margin: 0 0 8px; color: {self.FG_MUTED}; font-size: 13px;">
                                This email was sent to {self.escape(email)}
                            </p>
                            <p style="margin: 0; color: {self.FG_MUTED}; font-size: 12px;">
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

    def otp(self, code: str) -> str:
        """A one-time code on a card — matches landing-feature-card."""
        return f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px;">
                                <tr>
                                    <td align="center" style="background-color: {self.BG_MUTED}; border: 1px solid {self.BORDER}; border-radius: 12px; padding: 32px;">
                                        <div style="font-size: 42px; font-weight: 700; letter-spacing: 8px; background: {self.GRADIENT}; -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; font-family: {self.MONO_STACK};">
                                            {self.escape(code)}
                                        </div>
                                    </td>
                                </tr>
                            </table>"""

    def cta_button(self, *, text: str, url: str) -> str:
        """A call-to-action button — matches landing-btn-primary. Both are text."""
        return f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px;">
                                <tr>
                                    <td align="center">
                                        <a href="{self.escape(url)}" style="display: inline-block; background: {self.GRADIENT}; color: #ffffff; text-decoration: none; padding: 14px 40px; border-radius: 6px; font-size: 16px; font-weight: 600;">
                                            {self.escape(text)}
                                        </a>
                                    </td>
                                </tr>
                            </table>"""

    def callout(self, body: str, *, style: CalloutStyle) -> str:
        """A bordered callout around one paragraph of markup."""
        return self._callout_frame(
            f"""                                        <p style="margin: 0; color: {self.FG_DEFAULT}; font-size: 14px; line-height: 1.6;">
                                            {body}
                                        </p>""",
            style=style,
        )

    def callout_list(self, *, heading: str, items: tuple[str, ...], style: CalloutStyle) -> str:
        """A bordered callout holding a heading and a bulleted list of text."""
        bullets = '\n'.join(
            f'                                            <li>{self.escape(item)}</li>' for item in items
        )
        return self._callout_frame(
            f"""                                        <p style="margin: 0 0 10px; color: {self.FG_DEFAULT}; font-size: 14px; font-weight: 600;">{self.escape(heading)}</p>
                                        <ul style="margin: 0; padding-left: 20px; color: {self.FG_DEFAULT}; font-size: 14px; line-height: 1.8;">
{bullets}
                                        </ul>""",
            style=style,
        )

    def command(self, command: str, *, margin: str) -> str:
        """A shell command on a monospace card, with a muted prompt."""
        return f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: {margin};">
                                <tr>
                                    <td style="background-color: {self.BG_MUTED}; border: 1px solid {self.BORDER}; border-radius: 12px; padding: 16px 24px; font-family: {self.MONO_STACK}; font-size: 14px; color: {self.FG_DEFAULT};">
                                        <span style="color: {self.FG_MUTED};">$</span> {self.escape(command)}
                                    </td>
                                </tr>
                            </table>"""

    def section_label(self, text: str) -> str:
        """A small uppercase label — matches landing-section-label."""
        return f'                            <p style="margin: 0 0 8px; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: {self.ACCENT};">{self.escape(text)}</p>'

    def greeting(self, name: str | None) -> str:
        """'Hello Name,' or 'Hello,' when there is no name. The name is text."""
        return self.paragraph(f'Hello {self.escape(name)},' if name else 'Hello,')

    def paragraph(self, body: str, *, margin: str = '0 0 16px', muted: bool = False) -> str:
        """One paragraph of markup."""
        color = self.FG_MUTED if muted else self.FG_DEFAULT
        size = '13px' if muted else '15px'
        return f'                            <p style="margin: {margin}; color: {color}; font-size: {size}; line-height: 1.6;">{body}</p>'

    def _callout_frame(self, inner: str, *, style: CalloutStyle) -> str:
        return f"""                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 24px 0;">
                                <tr>
                                    <td style="background: {style.background}; border-left: 3px solid {style.border}; padding: 16px 20px; border-radius: 0 8px 8px 0;">
{inner}
                                    </td>
                                </tr>
                            </table>"""
