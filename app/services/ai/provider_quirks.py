"""Repairs applied to a chat payload before it goes upstream.

None of this is OpenAI's API. Every function here exists because one
provider rejects, or mishandles, something another one accepts, and the
alternative to repairing it is a 400 the caller cannot act on. It lived
in the router, which made the route handler the place you went to find
out what Nebius does differently.

The rule for everything in this module: it may *repair* a payload and
it may *drop* something unusable, but it must not invent content.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

#: How far back to walk when brute-forcing a truncated tool call.
#: Beyond this the arguments are too mangled to be worth guessing at.
_MAX_TRUNCATION = 500

#: Closing sequences tried against a truncated JSON object, in order of
#: how likely they are to be what was cut off. Deduplicated: both lists
#: previously carried `'"}'` twice, so one entry of each never ran.
_CLOSERS = ('"}', '"]}', '"}]}', '}')
_TRUNCATION_CLOSERS = ('"}', '}', '"]}')


def normalise_message(message: dict) -> None:
    """Ensure `content` is never null, preserving `reasoning_content`.

    Some providers (MiniMax-fast via Nebius, for one) stream thinking
    tokens as `reasoning_content` and leave `content` null. The two stay
    separate so a client that understands reasoning can render it
    differently, but `content` becomes at least `''` so a standard
    OpenAI client does not break on it.
    """
    if message.get('content') is None:
        message['content'] = ''


def normalise_choices(data: dict, key: str = 'message') -> None:
    """Apply `normalise_message` across a response or a stream chunk."""
    for choice in data.get('choices', []):
        part = choice.get(key)
        if isinstance(part, dict):
            normalise_message(part)


def repair_json_string(value: str) -> str:
    """Best-effort close of a truncated JSON object.

    A model that hits its token limit mid-tool-call emits arguments like
    `{"file_path": "x", "old_string": "...code...` — no closing quote,
    possibly missing keys, no brace. Upstream parses this field and
    rejects the whole request over it.

    Tries, in order: the string as-is, a few closing sequences, then
    truncating back one character at a time and closing that. An
    unrepairable value is wrapped as `{"_raw": ...}` rather than
    dropped, so whatever the model produced is still visible to whoever
    is debugging it.
    """
    value = value.rstrip()
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


def _parses(candidate: str) -> bool:
    try:
        json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def sanitise_message_sequence(body: dict) -> None:
    """Drop `tool` messages that no live `tool_calls` accounts for.

    Nebius and MiniMax require every `tool` message to sit in an
    unbroken `assistant(tool_calls) → tool → tool` run. A `tool` message
    after a plain assistant message is rejected even when its
    `tool_call_id` matches an earlier one — which happens when the CLI
    puts an assistant text message between a call and its response, or
    when history is truncated.

    An orphan is not deleted outright. Its content becomes a system
    message, because the result was real and the conversation reads
    wrong without it.

    That replacement stays a plain dict rather than a `ChatMessage`.
    Every other entry in this list is the caller's own unvalidated
    payload, passed through untouched; dumping a model here would put
    one differently-shaped message — carrying whatever defaults the
    model declares — in among them, on the wire to a provider that is
    already strict about this field.
    """
    messages = body.get('messages')
    if not messages:
        return

    active: set[str] = set()
    cleaned: list[dict] = []

    for message in messages:
        role = message.get('role')
        if role == 'assistant':
            tool_calls = message.get('tool_calls')
            active = {call.get('id') for call in tool_calls if call.get('id')} if tool_calls else set()
            cleaned.append(message)
        elif role == 'tool':
            if message.get('tool_call_id') in active:
                cleaned.append(message)
                continue
            content = message.get('content', '')
            if content:
                cleaned.append({'role': 'system', 'content': f'[Tool result]: {content}'})
        else:
            active = set()
            cleaned.append(message)

    if cleaned == messages:
        return
    dropped = len(messages) - len(cleaned)
    if dropped > 0:
        logger.warning('Dropped %d orphaned tool message(s)', dropped)
    body['messages'] = cleaned


def sanitise_tool_call_arguments(body: dict) -> None:
    """Make every `tool_calls[].function.arguments` parse."""
    for message in body.get('messages', []):
        for call in message.get('tool_calls') or []:
            function = call.get('function', {})
            arguments = function.get('arguments')
            if not isinstance(arguments, str) or _parses(arguments):
                continue
            function['arguments'] = repair_json_string(arguments)
