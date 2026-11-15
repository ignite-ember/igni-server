"""Mail goes out under the deployment's name, not the vendor's.

`docs/self-hosting.md` §6 tells an operator to set `EMAIL_FROM_NAME`
because "a self-hosted deployment is the customer's, and their people
should not receive sign-in codes from a vendor's brand". Only the From
display name followed it: every subject, the header and the footer said
`ignite-ember.sh`, whatever the operator set.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from app.config import settings
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


class _Captured:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> bool:
        self.messages.append(message)
        return True


@pytest.mark.parametrize('template', sorted(_EVERY_TEMPLATE))
def test_no_template_names_the_vendor(template):
    content = getattr(EmailTemplates(brand='Acme igni'), template)(**_EVERY_TEMPLATE[template])

    assert 'ignite-ember.sh' not in content.subject
    assert 'ignite-ember.sh' not in content.html_body
    assert 'Acme igni' in content.html_body


def test_the_brand_is_escaped_where_it_enters_markup():
    body = EmailTemplates(brand='<b>Acme</b>').sign_up(code='1', email='a@acme.test').html_body

    assert '<b>Acme</b>' not in body and '&lt;b&gt;Acme&lt;/b&gt;' in body


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
    assert 'ignite-ember.sh' not in welcome.as_string()
