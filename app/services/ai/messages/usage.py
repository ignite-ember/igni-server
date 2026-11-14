"""What a Messages request used, read from the provider's own count.

Anthropic reports the prompt in three parts — tokens read fresh, read
from the prompt cache, and written to it — and the output separately.
`input_tokens` here is the whole prompt, the three together, so it means
what `prompt_tokens` means on the OpenAI side; the two cache counts say
how much of it the cache served or stored.

A stream reports the prompt in `message_start` and the output, running
total, in each `message_delta`.
"""

from __future__ import annotations

import json
import time
from typing import Any

from pydantic import BaseModel, Field

from app.services.ai.token_estimate import TokenEstimator


class MessagesUsage(BaseModel):
    """One request's tokens: the prompt, its cached parts, and the output."""

    uncached: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    #: Whether the provider said anything at all; an estimate stands in when it did not.
    reported: bool = False

    @property
    def input_tokens(self) -> int:
        return self.uncached + self.cache_read + self.cache_write

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output

    def take(self, usage: dict[str, Any] | None) -> None:
        """Fold in a `usage` object; a field it does not carry keeps what was known."""
        if not isinstance(usage, dict):
            return
        fields = {
            'input_tokens': 'uncached',
            'cache_read_input_tokens': 'cache_read',
            'cache_creation_input_tokens': 'cache_write',
            'output_tokens': 'output',
        }
        for wire, mine in fields.items():
            value = usage.get(wire)
            if isinstance(value, int):
                setattr(self, mine, value)
                self.reported = True

    @classmethod
    def of_message(cls, message: dict[str, Any]) -> MessagesUsage:
        """The usage of a non-streamed answer."""
        usage = cls()
        usage.take(message.get('usage'))
        return usage


class StreamTally(BaseModel):
    """What a stream has used so far, and its text, for the estimate a silent provider needs."""

    t0: float = Field(default_factory=time.monotonic)
    usage: MessagesUsage = Field(default_factory=MessagesUsage)
    text: list[str] = Field(default_factory=list)
    recorded: bool = False

    @property
    def duration(self) -> float:
        return time.monotonic() - self.t0

    def absorb(self, event: dict[str, Any]) -> None:
        """Read one event's usage, and its text."""
        kind = event.get('type')
        if kind == 'message_start':
            self.usage.take((event.get('message') or {}).get('usage'))
        elif kind == 'message_delta':
            self.usage.take(event.get('usage'))
        elif kind == 'content_block_delta':
            delta = event.get('delta') or {}
            if isinstance(delta.get('text'), str):
                self.text.append(delta['text'])

    def start_over(self) -> None:
        """Another backend is about to answer: what this one produced is not billed."""
        self.usage = MessagesUsage()
        self.text.clear()

    def settle(self, *, estimator: TokenEstimator, body: dict[str, Any]) -> MessagesUsage:
        """The provider's own count when it gave one; otherwise an estimate from the text."""
        if self.usage.reported:
            return self.usage
        return estimate(estimator, body=body, output=''.join(self.text))


def estimate(estimator: TokenEstimator, *, body: dict[str, Any], output: str = '') -> MessagesUsage:
    """A rough count for a provider that reported none: the request as text, and the output."""
    encoder = estimator.encoder
    if encoder is None:
        return MessagesUsage()
    prompt = json.dumps([body.get('system'), body.get('messages')], ensure_ascii=False)
    return MessagesUsage(
        uncached=len(encoder.encode(prompt, disallowed_special=())),
        output=len(encoder.encode(output, disallowed_special=())),
    )
