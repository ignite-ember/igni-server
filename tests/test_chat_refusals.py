"""`ChatRefusals` is the one place a chat refusal becomes HTTP, `Retry-After` included."""

from __future__ import annotations

from fastapi import HTTPException
import pytest

from app.services.ai.chat.errors import (
    BackendMisconfiguredRefusal,
    ModelUnavailableRefusal,
    QueueFullRefusal,
    QueueTimeoutRefusal,
    UpstreamRefusal,
)
from app.services.ai.chat.refusals import ChatRefusals
from app.services.ai.upstream import UpstreamError, UpstreamUnreadable


def test_a_capacity_refusal_answers_429_and_says_when_to_retry():
    with pytest.raises(HTTPException) as raised, ChatRefusals():
        raise QueueFullRefusal()

    assert raised.value.status_code == 429
    assert raised.value.detail == ('Every inference node is at capacity and the queue is full. Please retry shortly.')
    assert raised.value.headers == {'Retry-After': '30'}
    assert isinstance(raised.value.__cause__, QueueFullRefusal)


def test_a_model_nobody_serves_answers_404_with_no_retry_after():
    with pytest.raises(HTTPException) as raised, ChatRefusals():
        raise ModelUnavailableRefusal('No node serves gpt-9')

    assert raised.value.status_code == 404
    assert raised.value.detail == 'No node serves gpt-9'
    assert raised.value.headers is None


def test_anything_else_passes_through_untouched():
    boom = RuntimeError('not a refusal')

    with pytest.raises(RuntimeError) as raised, ChatRefusals():
        raise boom

    assert raised.value is boom


def test_a_wait_that_ran_out_answers_429_in_its_own_words():
    with pytest.raises(HTTPException) as raised, ChatRefusals():
        raise QueueTimeoutRefusal()

    assert raised.value.status_code == 429
    assert raised.value.detail == 'Waited the longest a request may for a free inference node. Please retry.'
    assert raised.value.headers == {'Retry-After': '30'}


def test_a_misconfigured_deployment_answers_503_with_its_reason_verbatim():
    with pytest.raises(HTTPException) as raised, ChatRefusals():
        raise BackendMisconfiguredRefusal('No inference backends are available.')

    assert raised.value.status_code == 503
    assert raised.value.detail == 'No inference backends are available.'
    assert raised.value.headers is None


@pytest.mark.parametrize(
    ('failure', 'status_code', 'detail', 'headers'),
    [
        (UpstreamError(503, b'loading'), 502, 'The inference node returned 503.', None),
        (
            UpstreamError(429, b'busy'),
            429,
            'Every inference node is at capacity. Please retry shortly.',
            {'Retry-After': '30'},
        ),
        (UpstreamUnreadable('<html>'), 502, 'The inference node returned an unreadable response.', None),
    ],
)
def test_a_node_that_failed_answers_with_the_failure(failure, status_code, detail, headers):
    with pytest.raises(HTTPException) as raised, ChatRefusals():
        raise UpstreamRefusal(failure)

    assert (raised.value.status_code, raised.value.detail, raised.value.headers) == (status_code, detail, headers)
