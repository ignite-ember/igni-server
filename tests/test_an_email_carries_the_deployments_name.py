"""Mail goes out under the deployment's name, or under none — never the vendor's.

igni-server is shipped only as images the customer runs; the vendor runs
no server and signs nothing. `EMAIL_FROM_NAME` is therefore empty by
default, and an empty name means the mail names no sender at all: the
From header is the bare `EMAIL_FROM_ADDRESS`, no subject carries a
" — <brand>" suffix, the header and footer have no brand line, and the
sentences that named one read without it. Set, it appears as the From
display name and in every subject, header and footer
(`docs/self-hosting.md` §6).

The welcome mail used to be copy from the old hosted service — a public
beta, a Pro plan, a vendor model, a formula that no longer exists, "our
support team". None of that is true of a self-hosted install. Nor may any
mail name a public install command or URL: igni runs on isolated
networks, and how the app reaches desktops is the operator's call. The
only addresses a mail may carry are the deployment's own portal and the
operator's `CLIENT_INSTALL_URL`.
"""

from __future__ import annotations

from email.message import EmailMessage
import re

import pytest

from app.config import Settings, settings
from app.services.email.service import EmailService
from app.services.email.templates import EmailTemplates

pytestmark = pytest.mark.anyio

_EVERY_TEMPLATE = {
    'sign_up': {'code': '123456', 'email': 'a@acme.test'},
    'sign_in': {'code': '123456', 'email': 'a@acme.test'},
    'team_invite': {'email': 'a@acme.test', 'role': 'member', 'invite_url': 'https://portal.acme.test/x'},
    'password_reset': {'code': '123456', 'email': 'a@acme.test'},
    'welcome': {'email': 'a@acme.test'},
}

# Words that only the vendor's old hosted service could truthfully say.
_VENDOR_ONLY = (
    'ignite-ember.sh',
    'ignite-ember/tap/ignite-ember',
    'beta',
    'pro plan',
    'minimax',
    'support team',
    r'\bour team',
    'contact support',
    'we will never',
    'lite, pro',
    'brew install',
    'pip install',
    'pypi',
    'github.com',
)

_PORTAL = 'https://igni.acme.test'
_INSTALL = 'https://intranet.acme.test/software/igni'
_INVITE = 'https://portal.acme.test/x'
_URL = re.compile(r'https?://[^\s"<>\']+')


class _Captured:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> bool:
        self.messages.append(message)
        return True


def _text(html_body: str) -> str:
    """The body's visible words, tags dropped and whitespace collapsed."""
    spaced = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html_body))
    return re.sub(r' ([.,])', r'\1', spaced)


def _render(template: str, brand: str, *, install: str = ''):
    templates = EmailTemplates(brand=brand, portal_url=_PORTAL, client_install_url=install)
    return getattr(templates, template)(**_EVERY_TEMPLATE[template])


def test_the_default_name_is_empty():
    assert Settings.model_fields['email_from_name'].default == ''


def test_the_default_install_url_is_empty():
    assert Settings.model_fields['client_install_url'].default == ''


@pytest.mark.parametrize('template', sorted(_EVERY_TEMPLATE))
@pytest.mark.parametrize('install', ['', _INSTALL])
def test_no_template_carries_an_address_the_operator_did_not_configure(template, install):
    content = _render(template, '', install=install)
    configured = {_PORTAL, _INVITE} | ({install} if install else set())

    # The inline logo's SVG namespace is an identifier, never fetched.
    page = content.html_body.replace('xmlns="http://www.w3.org/2000/svg"', '')
    for url in _URL.findall(content.subject + page):
        assert url in configured, url


@pytest.mark.parametrize('template', sorted(_EVERY_TEMPLATE))
@pytest.mark.parametrize('brand', ['', 'Acme igni'])
def test_no_template_says_what_only_the_vendor_could(template, brand):
    content = _render(template, brand)

    for words in _VENDOR_ONLY:
        assert not re.search(words, content.subject.lower()), words
        assert not re.search(words, content.html_body.lower()), words


@pytest.mark.parametrize('template', sorted(_EVERY_TEMPLATE))
def test_with_no_name_nothing_dangles(template):
    content = _render(template, '')
    text = _text(content.html_body)

    assert ' — ' not in content.subject and not content.subject.rstrip().endswith('—')
    # No empty header subtitle or footer line is left behind.
    assert not re.search(r'<p[^>]*>\s*</p>', content.html_body)
    # No sentence lost its object: "signing up to .", "on .", "for ,".
    assert not re.search(r'\b(to|on|for|with)\s*[.,]', text), text
    assert '<strong></strong>' not in content.html_body


@pytest.mark.parametrize('template', sorted(_EVERY_TEMPLATE))
def test_a_set_name_is_in_the_subject_header_and_footer(template):
    content = _render(template, 'Acme igni')

    assert 'Acme igni' in content.html_body
    # header subtitle and footer: at least twice in the page.
    assert content.html_body.count('Acme igni') >= 2
    if template in ('sign_up', 'team_invite', 'welcome'):
        assert content.subject.endswith(' — Acme igni')


def test_sign_up_without_a_name_reads_naturally():
    text = _text(_render('sign_up', '').html_body)

    assert 'Thanks for signing up. Use the verification code below' in text
    assert "If you didn't sign up, please ignore this email." in text
    assert 'Nobody from your organisation will ask you for it.' in text


def test_the_invite_without_a_name_joins_the_organisations_team():
    text = _text(_render('team_invite', '').html_body)

    assert "You have been invited to join your organisation's team on igni as a Member." in text


def test_the_welcome_links_the_deployments_portal():
    content = _render('welcome', '')

    assert f'Your igni account is ready. Sign in at {_PORTAL}' in _text(content.html_body)
    assert f'href="{_PORTAL}"' in content.html_body


def test_the_welcome_links_the_operators_install_page_when_set():
    content = _render('welcome', '', install=_INSTALL)
    text = _text(content.html_body)

    assert f'Get the igni app: {_INSTALL}' in text
    assert f'href="{_INSTALL}"' in content.html_body
    assert 'how to get the igni app' not in text


def test_without_an_install_page_the_welcome_sends_people_to_their_administrator():
    text = _text(_render('welcome', '').html_body)

    assert 'Ask your administrator how to get the igni app.' in text
    assert 'Get the igni app:' not in text


def test_without_a_portal_address_the_welcome_links_nothing():
    content = EmailTemplates(brand='', portal_url='', client_install_url='').welcome(email='a@acme.test')

    assert 'Ask your administrator where to sign in.' in _text(content.html_body)
    assert 'href=' not in content.html_body


def test_the_brand_is_escaped_where_it_enters_markup():
    body = EmailTemplates(brand='<b>Acme</b>').sign_up(code='1', email='a@acme.test').html_body

    assert '<b>Acme</b>' not in body and '&lt;b&gt;Acme&lt;/b&gt;' in body


async def test_with_no_name_the_from_header_is_the_bare_address(monkeypatch):
    monkeypatch.setattr(settings, 'email_from_name', '')
    monkeypatch.setattr(settings, 'portal_base_url', _PORTAL + '/')
    monkeypatch.setattr(settings, 'client_install_url', '')
    transport = _Captured()
    service = EmailService(transport=transport, from_email='igni@acme.test')

    await service.send_sign_up_code(to_email='a@acme.test', code='654321')
    await service.send_welcome(to_email='a@acme.test')

    for message in transport.messages:
        assert message['From'] == 'igni@acme.test'
        assert ' — ' not in message['Subject']
        for words in _VENDOR_ONLY:
            assert not re.search(words, message.as_string().lower()), words
    plain = transport.messages[1].get_body(('plain',)).get_content()
    assert plain.splitlines() == [
        'Welcome! Your igni account is ready.',
        f'Sign in at {_PORTAL}',
        'Ask your administrator how to get the igni app.',
    ]


async def test_the_welcome_text_part_carries_the_install_page_when_set(monkeypatch):
    monkeypatch.setattr(settings, 'portal_base_url', _PORTAL)
    monkeypatch.setattr(settings, 'client_install_url', _INSTALL)
    transport = _Captured()

    await EmailService(transport=transport, from_email='igni@acme.test').send_welcome(to_email='a@acme.test')

    plain = transport.messages[0].get_body(('plain',)).get_content()
    assert f'Get the igni app: {_INSTALL}' in plain


async def test_the_service_brands_with_email_from_name(monkeypatch):
    monkeypatch.setattr(settings, 'email_from_name', 'R0 Igni')
    transport = _Captured()

    await EmailService(transport=transport, from_email='igni@acme.test').send_sign_up_code(
        to_email='a@acme.test', code='654321'
    )
    await EmailService(transport=transport, from_email='igni@acme.test').send_welcome(to_email='a@acme.test')

    sign_up, welcome = transport.messages
    assert sign_up['Subject'].endswith('Verify Your Email — R0 Igni')
    assert sign_up['From'] == 'R0 Igni <igni@acme.test>'
    assert welcome['Subject'].endswith('Welcome to igni — R0 Igni')
    assert 'ignite-ember.sh' not in welcome.as_string()
