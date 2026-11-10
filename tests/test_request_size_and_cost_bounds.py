"""A request cannot be arbitrarily large, or arbitrarily expensive.

Section 15 checked *authorization* on the chat path. Nothing had checked
what a request may weigh, and the answer was: anything.

Measured against a running API before the fix:

    body     HTTP   RSS delta
      2 MB    404     +3.5 MB
     17 MB    404    +13.5 MB
     65 MB    404    +50.3 MB
    193 MB    404   +134.7 MB

Every one was read and parsed in full, then rejected over the *model name*.
Size never entered into it. 200,000 messages were accepted the same way,
and so were `n=100000` and `max_tokens=10000000` — both forwarded straight
to the provider. Rate limiting is deliberately absent from this product, so
one authenticated caller is the whole threat model.

Two different problems, so two different fixes:

* **size** — `BodySizeLimitMiddleware` refuses a body over
  `settings.max_request_body_bytes` (32 MB). With `Content-Length` present
  nothing is read at all; without one, the stream is counted and stops at
  the cap.
* **cost** — `n` multiplies spend without adding a byte, so no size limit
  can catch it. Bounded in the schema instead.

32 MB is deliberately generous: this is a coding assistant and a
large-context request is legitimate. 1M tokens of text is roughly 4 MB.
"""

from __future__ import annotations

import json

import pytest

from app.config import settings
from tests.conftest import auth_header, create_user, make_token

_CHAT = '/chat/completions'


@pytest.fixture
async def caller(db):
    await create_user(db, user_id='bounds-1', email='bounds@example.com')
    return auth_header(make_token('bounds-1'))


def _body(content: str = 'hi', **extra) -> dict:
    return {'model': 'nonexistent-model', 'messages': [{'role': 'user', 'content': content}], **extra}


class TestTheCostKnobsAreBounded:
    """`n` is the one that multiplies spend rather than size, so the body
    limit cannot help. These assert on the *validation* layer, which is
    why a nonexistent model is fine — a 422 proves the request never got
    as far as choosing a backend."""

    @pytest.mark.parametrize('n', [17, 128, 100_000])
    async def test_an_absurd_n_is_refused(self, client, caller, n):
        response = await client.post(_CHAT, json=_body(n=n), headers=caller)

        assert response.status_code == 422, response.text
        assert any(err['loc'][-1] == 'n' for err in response.json()['detail'])

    @pytest.mark.parametrize('n', [1, 2, 16])
    def test_a_reasonable_n_is_allowed_through(self, n):
        """The bound must not become the bug: 16 is the ceiling and has to
        validate.

        Asserted against the schema rather than the endpoint. A valid
        request goes on to acquire an inference backend, and the test
        environment has none — so it raises `BackendConfigError` from well
        past the HTTP layer and there is no status code to read. The claim
        here is only that the schema admits the value.
        """
        from app.api.base_v1.schema.chat import ChatCompletionRequest

        ChatCompletionRequest.model_validate(_body(n=n))

    async def test_n_zero_is_refused(self, client, caller):
        response = await client.post(_CHAT, json=_body(n=0), headers=caller)

        assert response.status_code == 422

    @pytest.mark.parametrize('max_tokens', [1_000_001, 10_000_000])
    async def test_an_absurd_max_tokens_is_refused(self, client, caller, max_tokens):
        response = await client.post(_CHAT, json=_body(max_tokens=max_tokens), headers=caller)

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize('max_tokens', [1, 4096, 32768, 1_000_000])
    def test_a_real_max_tokens_is_allowed(self, max_tokens):
        from app.api.base_v1.schema.chat import ChatCompletionRequest

        ChatCompletionRequest.model_validate(_body(max_tokens=max_tokens))

    async def test_a_conversation_of_200000_turns_is_refused(self, client, caller):
        """The count that was accepted and forwarded."""
        body = {
            'model': 'nonexistent-model',
            'messages': [{'role': 'user', 'content': 'hi'}] * 200_000,
        }

        response = await client.post(_CHAT, json=body, headers=caller)

        assert response.status_code == 422, response.text[:200]

    def test_a_long_but_real_conversation_is_allowed(self):
        from app.api.base_v1.schema.chat import ChatCompletionRequest

        ChatCompletionRequest.model_validate(
            {'model': 'nonexistent-model', 'messages': [{'role': 'user', 'content': 'hi'}] * 500}
        )


class TestTheBodyLimitIsWiredIn:
    """The middleware's behaviour under a real oversized upload was verified
    against a running server (413, zero RSS growth on the Content-Length
    path). What is asserted here is that it is installed, positioned
    correctly, and configured — the parts a refactor breaks silently.
    """

    def test_the_middleware_is_registered(self):
        from app.main import app
        from app.middlewares.body_size_limit import BodySizeLimitMiddleware

        assert any(m.cls is BodySizeLimitMiddleware for m in app.user_middleware)

    def test_it_is_the_outermost_middleware(self):
        """Starlette applies middleware in reverse registration order, so
        the *last* registered is outermost. The first version of this was
        added before `TimingMiddleware`, which made it the innermost of the
        four and would have defeated the point — the body would already
        have been read by the time it ran.
        """
        from app.main import app
        from app.middlewares.body_size_limit import BodySizeLimitMiddleware

        assert app.user_middleware[0].cls is BodySizeLimitMiddleware, [m.cls.__name__ for m in app.user_middleware]

    def test_the_limit_is_configured_and_generous(self):
        """Generous on purpose: a coding assistant sends large contexts.
        Too small a limit is a worse bug than none, because it breaks real
        requests instead of hypothetical ones."""
        assert settings.max_request_body_bytes >= 8 * 1024 * 1024
        assert settings.max_request_body_bytes <= 128 * 1024 * 1024

    def test_it_refuses_a_zero_or_negative_limit(self):
        """A misconfiguration that would accept everything must not be
        constructible."""
        from app.middlewares.body_size_limit import BodySizeLimitMiddleware

        for bad in (0, -1):
            with pytest.raises(ValueError):
                BodySizeLimitMiddleware(app=None, max_bytes=bad)

    async def test_an_oversized_declared_body_is_413(self, client, caller):
        """Through the test client, which sets Content-Length — the path
        that costs nothing to refuse."""
        oversized = 'A' * (settings.max_request_body_bytes + 1024)

        response = await client.post(
            _CHAT,
            content=json.dumps(_body(oversized)),
            headers={**caller, 'Content-Type': 'application/json'},
        )

        assert response.status_code == 413, response.status_code
        detail = response.json()['detail']
        assert str(settings.max_request_body_bytes) in detail, detail

    async def test_a_body_just_under_the_limit_is_accepted(self, client, caller):
        """The limit has to admit what it claims to admit.

        Getting as far as the inference-backend lookup is the proof: the
        middleware passed the body on, FastAPI parsed it, and the endpoint
        ran. The test environment configures no backend, so that is where
        it stops — a 503 saying so, rather than a 413, is the result being
        asserted.
        """
        content = 'A' * (settings.max_request_body_bytes - 4096)

        response = await client.post(
            _CHAT,
            content=json.dumps(_body(content)),
            headers={**caller, 'Content-Type': 'application/json'},
        )

        assert response.status_code == 503, response.text


class TestTheProxyAgreesWithTheApi:
    def test_nginx_caps_the_body_too(self):
        """Unset, nginx applies its own default of 1 MB — so the portal path
        silently refused bodies the API accepts, and the two halves of the
        product disagreed about how large a request may be."""
        import pathlib
        import re

        conf = (pathlib.Path(__file__).resolve().parent.parent / 'portal/nginx.conf').read_text()
        match = re.search(r'client_max_body_size\s+(\d+)([kKmMgG]?)', conf)

        assert match, 'nginx does not cap the body, so its 1 MB default applies'

        size = int(match.group(1)) * {'': 1, 'k': 1024, 'm': 1024**2, 'g': 1024**3}[match.group(2).lower()]
        assert size == settings.max_request_body_bytes, (
            f'nginx allows {size} bytes but the API allows {settings.max_request_body_bytes}; the two must agree'
        )


class TestTheTwoLimitsAreOrdered:
    """There are now two size bounds, and their order matters.

    `BodySizeLimitMiddleware` is outermost; the group-entry bound from F24
    is inside it. Adding the outer one broke two F24 tests, which is how
    this came up: they sent a 32 MB entry expecting 422 and got 413,
    because 32 MB is exactly the outer limit. Their sizes moved to 4 MB.

    The invariant worth keeping is the ordering. If the entry limit ever
    exceeded the body limit it would become unreachable — every request
    large enough to violate it would be refused earlier, and the specific
    422 an admin needs to understand what went wrong would never be sent.
    """

    def test_the_entry_bound_sits_below_the_body_bound(self):
        from app.api.base_v1.schema.portal.group_entries import MAX_ENTRY_CONTENT_BYTES

        assert MAX_ENTRY_CONTENT_BYTES < settings.max_request_body_bytes, (
            f'the entry limit ({MAX_ENTRY_CONTENT_BYTES}) is not below the body limit '
            f'({settings.max_request_body_bytes}), so it can never be the reason a '
            f'request is refused — the 413 would always come first'
        )

    def test_there_is_room_between_them_to_test_the_inner_one(self):
        """Not just ordered, but far enough apart that a test can target
        the inner bound without tripping the outer one."""
        from app.api.base_v1.schema.portal.group_entries import MAX_ENTRY_CONTENT_BYTES

        assert MAX_ENTRY_CONTENT_BYTES * 4 < settings.max_request_body_bytes
