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

from app.services.ai.messages.events import AnthropicUsage, ClaudeEvent
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

    def take(self, usage: AnthropicUsage) -> None:
        """Fold in a `usage` object; a field it does not carry keeps what was known."""
        for given, mine in (
            (usage.input_tokens, 'uncached'),
            (usage.cache_read_input_tokens, 'cache_read'),
            (usage.cache_creation_input_tokens, 'cache_write'),
            (usage.output_tokens, 'output'),
        ):
            if given is not None:
                setattr(self, mine, given)
                self.reported = True

    @classmethod
    def of(cls, usage: AnthropicUsage) -> MessagesUsage:
        counted = cls()
        counted.take(usage)
        return counted

    @classmethod
    def estimated(cls, estimator: TokenEstimator, *, body: dict[str, Any], output: str = '') -> MessagesUsage:
        """A rough count for a provider that reported none: the request as text, and the output."""
        prompt = json.dumps([body.get('system'), body.get('messages')], ensure_ascii=False)
        prompt_tokens, output_tokens = estimator.count(prompt, output)
        return cls(uncached=prompt_tokens, output=output_tokens)


class StreamTally(BaseModel):
    """What a stream has used so far, and its text, for the estimate a silent provider needs."""

    t0: float = Field(default_factory=time.monotonic)
    usage: MessagesUsage = Field(default_factory=MessagesUsage)
    text: list[str] = Field(default_factory=list)
    recorded: bool = False

    @property
    def duration(self) -> float:
        return time.monotonic() - self.t0

    def absorb(self, event: ClaudeEvent) -> None:
        """Read one event's usage and its text — each event says what it carries."""
        usage = event.usage()
        if usage is not None:
            self.usage.take(usage)
        if piece := event.text():
            self.text.append(piece)

    def start_over(self) -> None:
        """Another backend is about to answer: what this one produced is not billed."""
        self.usage = MessagesUsage()
        self.text.clear()

    def settle(self, *, estimator: TokenEstimator, body: dict[str, Any]) -> MessagesUsage:
        """The provider's own count when it gave one; otherwise an estimate from the text."""
        if self.usage.reported:
            return self.usage
        return MessagesUsage.estimated(estimator, body=body, output=''.join(self.text))
