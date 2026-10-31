"""Caller text reaches these templates as HTML, and must arrive escaped.

An invitation interpolates the inviter's display name and the
recipient's name into markup. Both are chosen by people, and the email
goes to somebody else — so a name is the one field most likely to be
hostile and the one with the widest audience.

Escaping cannot live in the `_p` / `_info_box` / `_error_box` helpers:
their callers pass `<strong>` deliberately and the whole file would
render as literal angle brackets. It has to happen where caller data
enters, which is what `_esc` at the top of each template does.

`quote=True`, because several values land inside attribute positions
in `_wrap`.
"""

from __future__ import annotations

import pytest

from app.services.email.templates import EmailContent, get_team_invite_email_template

_HOSTILE = '<script>alert(1)</script>'


def _invite(**overrides) -> EmailContent:
    fields = {'email': 'new@example.com', 'role': 'member', 'invited_by': 'Ada Lovelace', 'name': 'Grace'}
    return get_team_invite_email_template(**{**fields, **overrides})


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

    def test_it_returns_a_named_pair(self):
        content = _invite()

        assert content.subject and content.html_body
        assert 'ignite-ember' in content.subject
