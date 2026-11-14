"""The parts of Anthropic's stream events this server reads: usage, and the text.

A stream is forwarded to the caller exactly as the provider sent it; these
models are only what the server reads on the way past, so a field they do
not name is ignored rather than refused. Each event says for itself what it
adds to the tally (`StreamTally`), so nothing dispatches on its type.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class AnthropicUsage(BaseModel):
    """A `usage` object: the prompt in three parts, and the output. A field it does not carry is None."""

    model_config = ConfigDict(extra='ignore')

    input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    output_tokens: int | None = None


class _Event(BaseModel):
    model_config = ConfigDict(extra='ignore')

    def usage(self) -> AnthropicUsage | None:
        """The usage this event reports, if any."""
        return None

    def text(self) -> str:
        """The output text this event adds."""
        return ''


class _MessageBody(BaseModel):
    model_config = ConfigDict(extra='ignore')

    usage: AnthropicUsage = Field(default_factory=AnthropicUsage)


class MessageStart(_Event):
    """The stream's first event: the prompt's usage."""

    type: Literal['message_start']
    message: _MessageBody = Field(default_factory=_MessageBody)

    def usage(self) -> AnthropicUsage | None:
        return self.message.usage


class MessageDelta(_Event):
    """The output's running total, and how the message ended."""

    type: Literal['message_delta']
    usage_: AnthropicUsage = Field(default_factory=AnthropicUsage, alias='usage')

    def usage(self) -> AnthropicUsage | None:
        return self.usage_


class _Delta(BaseModel):
    model_config = ConfigDict(extra='ignore')

    text: str = ''


class ContentBlockDelta(_Event):
    """A piece of a content block; a text delta carries the output's words."""

    type: Literal['content_block_delta']
    delta: _Delta = Field(default_factory=_Delta)

    def text(self) -> str:
        return self.delta.text


class OtherEvent(_Event):
    """Any event the server does not read: a ping, a block's start or stop, the message's stop."""

    type: str


#: An event as the server reads it; one it does not know is an `OtherEvent`.
ClaudeEvent = MessageStart | MessageDelta | ContentBlockDelta | OtherEvent

_KNOWN = TypeAdapter(Annotated[MessageStart | MessageDelta | ContentBlockDelta, Field(discriminator='type')])


def event_of(data: object) -> ClaudeEvent | None:
    """The event a parsed SSE payload is, or None for a payload that is not an event at all."""
    try:
        return _KNOWN.validate_python(data)
    except ValidationError:
        pass
    try:
        return OtherEvent.model_validate(data)
    except ValidationError:
        return None


class ProviderMessage(BaseModel):
    """A non-streamed answer, as far as the server reads it: that it is a message, and its usage.

    The caller is sent the provider's JSON as it came; this is only the check
    that it is a message, and the count to bill.
    """

    model_config = ConfigDict(extra='ignore')

    type: Literal['message']
    usage: AnthropicUsage = Field(default_factory=AnthropicUsage)


class ProviderReply(BaseModel):
    """A non-streamed answer: what the server reads of it, and the JSON as the provider sent it.

    The JSON is forwarded untouched — Claude's fields this server does not
    model must reach the caller as they came — so it stays a raw object.
    """

    model_config = ConfigDict(frozen=True)

    message: ProviderMessage
    sent: dict[str, Any]
