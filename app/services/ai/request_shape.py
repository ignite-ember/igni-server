"""Describe a chat request without writing the prompt to a log.

F111. `/v1/chat/completions` logged `json.dumps(body)` when the upstream
answered 400 — and the body of a chat completion **is the prompt**: the
user's question, whatever source code the CLI attached as context, and
the tool output from previous turns. The most sensitive content this
product handles, on the path production actually runs.

The log line even said `(first 500 chars)`, which `json.dumps(body)` does
not do. So an operator reading it would believe the exposure was bounded
at 500 characters while a 100 KB prompt went in whole.

F83 established the rule for exactly this — describe the shape, never the
content — and its AST guard would have caught this line on the day it was
written. It was scoped to the two webhook directories. This module is
the chat half of the same idea.

**What an operator needs from an upstream 400 is the shape.** Which model
was asked for, how many messages, in what roles, how big, whether tools
were involved, and whether the sequence was well-formed — a 400 from vLLM
is nearly always a malformed message sequence, a tool_call without its
reply, or a context length. None of those questions need the text.
"""

from __future__ import annotations

from typing import Any


def describe(body: dict[str, Any]) -> str:
    """A one-line, content-free description of a chat completion body.

    Structural facts only: names of keys, counts, roles and sizes. Role
    names come from the API's own schema rather than from user input, so
    they are safe; message *content* never appears.
    """
    messages = body.get('messages')
    parts: list[str] = [f'model={body.get("model")!r}']

    if isinstance(messages, list):
        roles: list[str] = []
        content_chars = 0
        tool_calls = 0
        for message in messages:
            if not isinstance(message, dict):
                roles.append('?')
                continue
            roles.append(str(message.get('role', '?')))
            content = message.get('content')
            if isinstance(content, str):
                content_chars += len(content)
            elif isinstance(content, list):
                # The multi-part form: count the parts and their sizes,
                # never their text.
                for chunk in content:
                    if isinstance(chunk, dict) and isinstance(chunk.get('text'), str):
                        content_chars += len(chunk['text'])
            calls = message.get('tool_calls')
            if isinstance(calls, list):
                tool_calls += len(calls)

        parts.append(f'messages={len(messages)}')
        # The sequence, which is what a 400 is usually about: an
        # assistant tool_call with no tool reply after it, or a tool
        # message with nothing before it.
        parts.append('roles=' + '>'.join(roles[:24]) + ('>…' if len(roles) > 24 else ''))
        parts.append(f'content_chars={content_chars}')
        if tool_calls:
            parts.append(f'tool_calls={tool_calls}')
    else:
        parts.append(f'messages={type(messages).__name__}')

    if body.get('tools'):
        tools = body['tools']
        parts.append(f'tools={len(tools) if isinstance(tools, list) else type(tools).__name__}')
    if body.get('stream'):
        parts.append('stream=true')
    for numeric in ('max_tokens', 'temperature', 'top_p'):
        if body.get(numeric) is not None:
            parts.append(f'{numeric}={body[numeric]}')

    return ' '.join(parts)
