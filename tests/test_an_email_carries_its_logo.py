"""The igni icon travels inside each message, not as markup a client strips.

Gmail and Outlook drop inline SVG, and an air-gapped deployment has no
public URL to load an image from. So the header shows `cid:igni-logo`
and the message carries that PNG as an inline part of the HTML body.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from app.services.email.markup import EmailMarkup
from app.services.email.service import EmailService

pytestmark = pytest.mark.anyio


class _Captured:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> bool:
        self.messages.append(message)
        return True


def _images(message: EmailMessage) -> list[EmailMessage]:
    return [part for part in message.walk() if part.get_content_type() == 'image/png']


async def _sent(**send) -> EmailMessage:
    transport = _Captured()
    service = EmailService(transport=transport, from_email='igni@acme.test')
    if send:
        await service.send_email(to_email='a@acme.test', subject='Subject', **send)
    else:
        await service.send_sign_up_code(to_email='a@acme.test', code='654321')
    return transport.messages[0]


async def test_a_templated_email_carries_the_logo_it_shows():
    message = await _sent()

    html = message.get_body(('html',)).get_content()
    assert 'src="cid:igni-logo"' in html
    assert '<svg' not in html
    [logo] = _images(message)
    assert logo['Content-ID'] == '<igni-logo>'
    assert logo.get_content_disposition() == 'inline'
    assert logo.get_content() == EmailMarkup.LOGO_PNG.read_bytes()


async def test_the_logo_sits_with_the_html_and_the_text_part_stays_plain():
    message = await _sent()

    [related] = [part for part in message.walk() if part.get_content_type() == 'multipart/related']
    assert [part.get_content_type() for part in related.iter_parts()] == ['text/html', 'image/png']
    assert message.get_body(('plain',)).get_content().strip()


async def test_the_logo_also_rides_an_html_only_message():
    page = EmailMarkup().page(title='t', subtitle='', content='', email='a@acme.test', footer='')

    message = await _sent(html_body=page)

    assert message.get_content_type() == 'multipart/related'
    assert len(_images(message)) == 1


async def test_a_message_that_does_not_show_the_logo_does_not_carry_it():
    message = await _sent(html_body='<p>Body</p>', text_body='plain')

    assert _images(message) == []


def test_the_logo_is_a_png_twice_the_size_it_is_shown_at():
    data = EmailMarkup.LOGO_PNG.read_bytes()

    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    assert (int.from_bytes(data[16:20]), int.from_bytes(data[20:24])) == (64, 64)
    assert 'width="32" height="32"' in EmailMarkup.LOGO_IMG
