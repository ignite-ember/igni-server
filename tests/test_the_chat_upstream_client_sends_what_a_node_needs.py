"""What the chat proxy sends a node: the URL, the key, the body, and how long it waits.

The node's URL and headers were built inline in the chat route and are
now the lease's; the call itself is `UpstreamChatClient`. Every other
chat test leases a node with no key, so these are where a keyed node,
a base URL with a trailing slash, and the configured timeout are seen.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.config import settings
from app.services.ai.chat.upstream_client import STREAM_TIMEOUT, UpstreamChatClient
from app.services.ai.schemas import BackendLease
from app.services.ai.upstream import UpstreamError
from tests.chat_proxy import drain, request

_COMPLETION = {'id': 'x', 'choices': [], 'usage': {}}


def _lease(*, base_url: str = 'http://node.invalid/v1', api_key: str | None = None) -> BackendLease:
    return BackendLease(lease_id='l', backend_id='b', base_url=base_url, model='fake-model', api_key=api_key)


class _Recorder:
    """A client factory that remembers each timeout and request, and answers *reply*."""

    def __init__(self, reply: httpx.Response) -> None:
        self.reply = reply
        self.timeouts: list[object] = []
        self.requests: list[httpx.Request] = []

    def __call__(self, **kwargs) -> httpx.AsyncClient:
        self.timeouts.append(kwargs.get('timeout'))
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer), **kwargs)

    def _answer(self, sent: httpx.Request) -> httpx.Response:
        self.requests.append(sent)
        return self.reply


class TestTheLeaseNamesTheNode:
    @pytest.mark.parametrize('base_url', ['http://node.invalid/v1', 'http://node.invalid/v1/'])
    def test_the_completions_url_has_one_slash(self, base_url):
        assert _lease(base_url=base_url).completions_url == 'http://node.invalid/v1/chat/completions'

    def test_a_node_with_a_key_is_sent_it_as_a_bearer_token(self):
        assert _lease(api_key='sk-node').auth_headers() == {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer sk-node',
        }

    @pytest.mark.parametrize('api_key', [None, ''])
    def test_a_node_without_one_gets_no_authorization_at_all(self, api_key):
        """Not `Bearer None`: a vLLM on a private network has no auth."""
        assert _lease(api_key=api_key).auth_headers() == {'Content-Type': 'application/json'}


class TestACompletion:
    async def test_goes_to_the_nodes_url_with_its_key_and_the_body(self):
        recorder = _Recorder(httpx.Response(200, json=_COMPLETION))
        sent_request = request()

        await UpstreamChatClient(client_factory=recorder).complete(
            lease=_lease(base_url='http://node.invalid/v1/', api_key='sk-node'), request=sent_request
        )

        (sent,) = recorder.requests
        assert (sent.method, str(sent.url)) == ('POST', 'http://node.invalid/v1/chat/completions')
        assert sent.headers['authorization'] == 'Bearer sk-node'
        assert sent.headers['content-type'] == 'application/json'
        assert json.loads(sent.content) == sent_request.wire()

    async def test_without_a_key_sends_no_authorization(self):
        recorder = _Recorder(httpx.Response(200, json=_COMPLETION))

        await UpstreamChatClient(client_factory=recorder).complete(lease=_lease(), request=request())

        assert 'authorization' not in recorder.requests[0].headers

    async def test_waits_as_long_as_the_setting_says_at_the_time_of_the_call(self, monkeypatch):
        """The client lives as long as the app; the setting is read per call,
        as it was when the route built its own `httpx` client each time."""
        recorder = _Recorder(httpx.Response(200, json=_COMPLETION))
        client = UpstreamChatClient(client_factory=recorder)

        monkeypatch.setattr(settings.ai, 'upstream_timeout', 12.5)
        await client.complete(lease=_lease(), request=request())
        monkeypatch.setattr(settings.ai, 'upstream_timeout', 3.0)
        await client.complete(lease=_lease(), request=request())

        assert recorder.timeouts == [12.5, 3.0]

    async def test_a_timeout_given_to_the_client_wins(self, monkeypatch):
        monkeypatch.setattr(settings.ai, 'upstream_timeout', 12.5)
        recorder = _Recorder(httpx.Response(200, json=_COMPLETION))

        await UpstreamChatClient(client_factory=recorder, complete_timeout=5).complete(
            lease=_lease(), request=request()
        )

        assert recorder.timeouts == [5]


class TestAStream:
    async def test_goes_to_the_nodes_url_with_its_key_and_the_stream_timeouts(self):
        recorder = _Recorder(httpx.Response(200, text='data: [DONE]\n\n'))
        sent_request = request(stream=True)

        lines = await drain(
            UpstreamChatClient(client_factory=recorder).stream(lease=_lease(api_key='sk-node'), request=sent_request)
        )

        assert [line.frame for line in lines] == ['data: [DONE]\n\n']
        (sent,) = recorder.requests
        assert str(sent.url) == 'http://node.invalid/v1/chat/completions'
        assert sent.headers['authorization'] == 'Bearer sk-node'
        assert json.loads(sent.content) == sent_request.wire()
        assert recorder.timeouts == [STREAM_TIMEOUT]

    async def test_a_400_logs_the_requests_shape_and_never_its_prompt(self, caplog):
        recorder = _Recorder(httpx.Response(400, text='bad sequence'))
        secret = 'the source the CLI attached'

        with caplog.at_level(logging.ERROR), pytest.raises(UpstreamError) as caught:
            await drain(
                UpstreamChatClient(client_factory=recorder).stream(
                    lease=_lease(), request=request([{'role': 'user', 'content': secret}], stream=True)
                )
            )

        assert caught.value.status_code == 400
        assert 'Upstream 400 — request ' in caplog.text
        assert secret not in caplog.text
