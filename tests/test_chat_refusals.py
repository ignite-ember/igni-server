"""`ChatRefusals` is the one place a chat refusal becomes HTTP, `Retry-After` included."""

from __future__ import annotations

from fastapi import HTTPException
import pytest

from app.services.ai.chat.errors import ModelUnavailableRefusal, QueueFullRefusal
from app.services.ai.chat.refusals import ChatRefusals


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
