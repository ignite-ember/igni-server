"""Caller text reaches these templates as HTML, and must arrive escaped.

An invitation interpolates the inviter's display name and the
recipient's name into markup. Both are chosen by people, and the email
goes to somebody else — so a name is the one field most likely to be
hostile and the one with the widest audience.

Escaping cannot live in `EmailMarkup.paragraph` or the callouts: their
callers pass `<strong>` deliberately and the whole email would render as
literal angle brackets. Blocks that take text (the greeting, the footer
address, the button) escape it; text interpolated into markup is escaped
by the template where it enters.

`quote=True`, because some values land inside attribute positions.
"""

from __future__ import annotations

import pytest

from app.services.email.templates import EmailContent, EmailTemplates

_HOSTILE = '<script>alert(1)</script>'


def _invite(**overrides) -> EmailContent:
    fields = {
        'email': 'new@example.com',
        'role': 'member',
        'invite_url': 'https://portal.example.com/signup?email=new%40example.com',
        'invited_by': 'Ada Lovelace',
        'name': 'Grace',
    }
    return EmailTemplates().team_invite(**{**fields, **overrides})


class TestNoCallerTextBecomesMarkup:
    @pytest.mark.parametrize('field', ['invited_by', 'name'])
    def test_a_script_tag_is_escaped(self, field):
        body = _invite(**{field: _HOSTILE}).html_body

        assert _HOSTILE not in body, f'{field} reached the email as markup'
        assert '&lt;script&gt;' in body

    def test_an_attribute_breakout_is_escaped(self):
        """`quote=True`: several of these land inside attributes."""
        body = _invite(invited_by='" onmouseover="alert(1)').html_body

        assert 'onmouseover="alert(1)"' not in body
        assert '&quot;' in body

    def test_an_unbalanced_angle_bracket_does_not_eat_the_rest(self):
        """The quieter failure. A name containing a bare `<` used to
        swallow the markup that followed it, so the email rendered
        correctly right up until somebody's display name."""
        body = _invite(invited_by='Ada <Lovelace').html_body

        assert body.rstrip().endswith('</html>')
        assert '&lt;Lovelace' in body


class TestTheTemplatesStillRender:
    def test_ordinary_text_is_untouched(self):
        body = _invite(invited_by='Ada Lovelace').html_body

        assert 'Ada Lovelace' in body

    def test_the_markup_the_helpers_add_is_not_escaped(self):
        """The reason escaping is at the entry point rather than in the
        block helpers: those take markup on purpose."""
        body = _invite().html_body

        assert '<strong>' in body
        assert '&lt;strong&gt;' not in body

    def test_the_invite_link_is_the_one_it_was_given(self):
        """The template used to build its own link from the HTML-escaped
        address, unencoded — `a+b@x` arrived at sign-up as `a b@x`."""
        url = 'https://portal.example.com/signup?email=a%2Bb%40example.com'
        body = _invite(email='a+b@example.com', invite_url=url).html_body

        assert f'href="{url}"' in body

    @pytest.mark.parametrize('field', ['email', 'role'])
    def test_other_caller_text_is_escaped_too(self, field):
        body = _invite(**{field: _HOSTILE}).html_body

        assert _HOSTILE not in body

    def test_it_returns_a_named_pair(self):
        content = _invite()

        assert content.subject and content.html_body
        assert 'ignite-ember' in content.subject
