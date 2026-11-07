"""What the chat proxy answers a request with, for the router to turn into HTTP."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.api.base_v1.schema.chat import ChatCompletionResponse
from app.services.ai.chat.streaming import CompletionStream


class _Answer(BaseModel):
    """What every answer shares: it is a value, decided once."""

    model_config = ConfigDict(frozen=True)


class CallerLeft(_Answer):
    """The caller left while waiting for a node or for the completion; nothing is owed them."""


class StreamedAnswer(_Answer):
    """A stream, opened and not yet started: its frames run as the response is sent."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    stream: CompletionStream


class CompletedAnswer(_Answer):
    """The whole completion, generated while the caller waited."""

    response: ChatCompletionResponse


#: One request's outcome. A closed union, so a `match` over it is checked for a missing arm.
type ChatAnswer = CallerLeft | StreamedAnswer | CompletedAnswer
