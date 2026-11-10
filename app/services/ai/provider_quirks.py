"""Repairs to a chat payload on its way to a provider, and to what comes back.

None of this is OpenAI's API. Everything here exists because one
provider rejects, or mishandles, something another one accepts, and the
alternative to repairing it is a 400 the caller cannot act on.

The rule for everything in this module: it may *repair* a payload and
it may *drop* something unusable, but it must not invent content.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.base_v1.schema.chat import ChatMessage, ToolCall

logger = logging.getLogger(__name__)

#: How far back to walk when brute-forcing a truncated tool call.
#: Beyond this the arguments are too mangled to be worth guessing at.
_MAX_TRUNCATION = 500

#: Closing sequences tried against a truncated JSON object, in order of
#: how likely they are to be what was cut off.
_CLOSERS = ('"}', '"]}', '"}]}', '}')
_TRUNCATION_CLOSERS = ('"}', '}', '"]}')


def _parses(candidate: str) -> bool:
    try:
        json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return False
    return True


class TruncatedJson(BaseModel):
    """A JSON object a model may have stopped emitting halfway through.

    A model that hits its token limit mid-tool-call emits arguments like
    `{"file_path": "x", "old_string": "...code...` — no closing quote,
    possibly missing keys, no brace. Upstream parses this field and
    rejects the whole request over it.
    """

    value: str

    def closed(self) -> str:
        """The value as a parseable JSON object, by the least change that gets there.

        Tries, in order: the string as-is, a few closing sequences, then
        truncating back one character at a time and closing that. An
        unrepairable value is wrapped as `{"_raw": ...}` rather than
        dropped, so whatever the model produced is still visible to
        whoever is debugging it.
        """
        value = self.value.rstrip()
        if _parses(value):
            return value

        for closer in _CLOSERS:
            if _parses(value + closer):
                return value + closer

        for end in range(len(value) - 1, max(0, len(value) - _MAX_TRUNCATION), -1):
            candidate = value[:end]
            for closer in _TRUNCATION_CLOSERS:
                if _parses(candidate + closer):
                    return candidate + closer

        return json.dumps({'_raw': value})


class UpstreamConversation(BaseModel):
    """The messages a request sends upstream, repaired for the strictest provider."""

    messages: list[ChatMessage]

    def repaired(self) -> list[ChatMessage]:
        """Every tool call's arguments parse, and every tool result sits in its run."""
        return self._results_in_their_run([self._with_parseable_arguments(message) for message in self.messages])

    @classmethod
    def _with_parseable_arguments(cls, message: ChatMessage) -> ChatMessage:
        """The message with each unparseable string `arguments` closed; the message itself when none is."""
        if not message.tool_calls:
            return message
        calls = [cls._with_closed_arguments(call) for call in message.tool_calls]
        if all(new is old for new, old in zip(calls, message.tool_calls)):
            return message
        return message.model_copy(update={'tool_calls': calls})

    @staticmethod
    def _with_closed_arguments(call: ToolCall) -> ToolCall:
        """The call itself when its arguments parse or are not a string; otherwise a copy with them closed."""
        function = call.function
        if function is None or not isinstance(function.arguments, str) or _parses(function.arguments):
            return call
        closed = TruncatedJson(value=function.arguments).closed()
        return call.model_copy(update={'function': function.model_copy(update={'arguments': closed})})

    @staticmethod
    def _results_in_their_run(messages: list[ChatMessage]) -> list[ChatMessage]:
        """Drop `tool` messages that no live `tool_calls` accounts for.

        Nebius and MiniMax require every `tool` message to sit in an
        unbroken `assistant(tool_calls) → tool → tool` run. A `tool`
        message after a plain assistant message is rejected even when its
        `tool_call_id` matches an earlier one — which happens when the CLI
        puts an assistant text message between a call and its response,
        or when history is truncated.

        An orphan is not deleted outright. Its content becomes a system
        message, because the result was real and the conversation reads
        wrong without it.
        """
        active: set[str] = set()
        cleaned: list[ChatMessage] = []

        for message in messages:
            if message.role == 'assistant':
                active = {call.id for call in message.tool_calls or [] if call.id}
                cleaned.append(message)
            elif message.role == 'tool':
                if message.tool_call_id in active:
                    cleaned.append(message)
                elif message.content:
                    cleaned.append(ChatMessage(role='system', content=f'[Tool result]: {message.content}'))
            else:
                active = set()
                cleaned.append(message)

        dropped = len(messages) - len(cleaned)
        if dropped > 0:
            logger.warning('Dropped %d orphaned tool message(s)', dropped)
        return cleaned


def _content_never_null(data: Any) -> Any:
    """A message or delta whose `content` is null or missing gets `''`.

    Some providers (MiniMax-fast via Nebius, for one) stream thinking
    tokens as `reasoning_content` and leave `content` null. The two stay
    separate so a client that understands reasoning can render it
    differently, but `content` becomes at least `''` so a standard
    OpenAI client does not break on it.
    """
    if isinstance(data, dict) and data.get('content') is None:
        return {**data, 'content': ''}
    return data


class RepliedMessage(ChatMessage):
    """A message in a node's non-streamed answer, its `content` never null."""

    @model_validator(mode='before')
    @classmethod
    def _content_is_never_null(cls, data: Any) -> Any:
        return _content_never_null(data)


class ForwardedDelta(BaseModel):
    """A streamed choice's delta as forwarded to the client, its `content` never null."""

    model_config = ConfigDict(extra='allow')

    content: str | list

    @model_validator(mode='before')
    @classmethod
    def _content_is_never_null(cls, data: Any) -> Any:
        return _content_never_null(data)


class ForwardedChoice(BaseModel):
    """A streamed choice as forwarded to the client."""

    model_config = ConfigDict(extra='allow')

    delta: ForwardedDelta | None = None


class ForwardedChunk(BaseModel):
    """One streamed chunk as forwarded to the client: what the node sent, with each delta repaired."""

    model_config = ConfigDict(extra='allow')

    choices: list[ForwardedChoice] = Field(default_factory=list)

    def wire(self) -> dict[str, Any]:
        """The JSON object, with only the keys the node sent (plus each delta's `content`)."""
        return self.model_dump(exclude_unset=True)
