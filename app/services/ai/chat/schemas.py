"""The values a chat request carries: who asked, what goes upstream, what comes back."""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from app.api.base_v1.schema.chat import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    CompletionUsage,
)
from app.services.ai.provider_quirks import normalise_choices, sanitise_message_sequence, sanitise_tool_call_arguments
from app.services.ai.token_estimate import TokenEstimator


class ChatCaller(BaseModel):
    """Who a chat request is for, and whose usage it is."""

    user_id: str
    #: Null only for a person in no organisation yet.
    org_id: str | None
    #: An app's own id, or a space's app; None for a person.
    app_id: str | None = None
    #: An app or a space rather than a person: never given a group's model.
    is_machine: bool = False


class StreamOptions(BaseModel):
    """The `stream_options` this server asks for when the caller sent none."""

    model_config = ConfigDict(extra='allow')

    include_usage: bool | None = None


class UpstreamChatRequest(ChatCompletionRequest):
    """The body sent to a node: the caller's request, repaired, with the model chosen.

    The model lives only here, so the model sent and the model billed
    cannot drift apart. The caller's request was bounded on the way in;
    these overrides drop the bounds, because a repair may legitimately
    leave a shape the inbound check would refuse.
    """

    model: str
    messages: list[ChatMessage]
    n: int | None = None
    max_tokens: int | None = None

    @classmethod
    def for_upstream(cls, body: ChatCompletionRequest, *, model: str) -> UpstreamChatRequest:
        """The caller's *body*, for *model*, with each provider's repairs applied once.

        A caller's own `stream_options` passes through as sent; only a
        stream that has none asks for usage.
        """
        draft = body.model_dump(exclude_none=True)
        draft['model'] = model
        sanitise_tool_call_arguments(draft)
        sanitise_message_sequence(draft)
        if body.stream:
            draft.setdefault('stream_options', StreamOptions(include_usage=True).model_dump(exclude_none=True))
        return cls.model_validate(draft)

    def retarget(self, model: str) -> UpstreamChatRequest:
        """The same request for a different node's model."""
        return self.model_copy(update={'model': model})

    def wire(self) -> dict[str, Any]:
        """The JSON body, at the one point it leaves as a dict for `httpx`."""
        return self.model_dump(exclude_none=True)


def _empty_usage_is_none(value: object) -> object:
    """An empty `usage` object says nothing; it must not zero counts already captured."""
    return value or None


#: A node's `usage` block, where `{}` is no report at all.
ReportedUsage = Annotated[CompletionUsage | None, BeforeValidator(_empty_usage_is_none)]


class UpstreamChoice(BaseModel):
    """One choice of a node's non-streamed answer."""

    index: int | None = None
    message: ChatMessage
    finish_reason: str | None = None


class UpstreamCompletion(BaseModel):
    """A node's non-streamed 200, validated."""

    id: str = ''
    object: str = 'chat.completion'
    created: int = 0
    model: str = ''
    choices: list[UpstreamChoice] = Field(default_factory=list)
    usage: ReportedUsage = None

    @classmethod
    def from_raw(cls, data: Any) -> UpstreamCompletion:
        """Normalise the provider's payload — whatever JSON it sent — then validate it. Raises `ValidationError`."""
        if isinstance(data, dict):
            normalise_choices(data, key='message')
        return cls.model_validate(data)

    def to_response(self) -> ChatCompletionResponse:
        """The answer this server gives; a choice with no index takes its position."""
        return ChatCompletionResponse(
            id=self.id,
            object=self.object,
            created=self.created,
            model=self.model,
            choices=[
                ChatCompletionChoice(
                    index=choice.index if choice.index is not None else position,
                    message=choice.message,
                    finish_reason=choice.finish_reason,
                )
                for position, choice in enumerate(self.choices)
            ],
            usage=self.usage,
        )


class StreamDelta(BaseModel):
    """What one chunk adds to a choice. Only the text is read."""

    content: str | None = None


class StreamChoice(BaseModel):
    delta: StreamDelta | None = None


class StreamChunk(BaseModel):
    """One parsed SSE data line: its text pieces and, at the end, the node's usage."""

    choices: list[StreamChoice] = Field(default_factory=list)
    usage: ReportedUsage = None


class UpstreamLine(BaseModel):
    """One frame from a node's stream, ready to forward, with its chunk when it carried one."""

    frame: str
    chunk: StreamChunk | None = None

    @classmethod
    def from_sse(cls, line: str) -> UpstreamLine | None:
        """The frame for one upstream line; None for a blank one.

        Comments, `[DONE]` and anything unparseable pass through as sent.
        A data line is normalised before it is forwarded, so a client
        never sees a null `content`.
        """
        if not line:
            return None
        if not line.startswith('data: ') or line == 'data: [DONE]':
            return cls(frame=f'{line}\n\n')
        try:
            raw = json.loads(line[6:])
        except json.JSONDecodeError:
            return cls(frame=f'{line}\n\n')
        if isinstance(raw, dict):
            normalise_choices(raw, key='delta')
        try:
            chunk = StreamChunk.model_validate(raw)
        except ValidationError:
            chunk = None
        return cls(frame=f'data: {json.dumps(raw)}\n\n', chunk=chunk)


class StreamUsage(BaseModel):
    """What a stream has used so far: the row written however it ends."""

    t0: float = Field(default_factory=time.monotonic)
    reported: CompletionUsage | None = None
    output_pieces: list[str] = Field(default_factory=list)
    recorded: bool = False

    @property
    def duration(self) -> float:
        return time.monotonic() - self.t0

    def absorb(self, chunk: StreamChunk) -> None:
        """Take the node's usage when it reports one, and every piece of text."""
        if chunk.usage is not None:
            self.reported = chunk.usage
        for choice in chunk.choices:
            if choice.delta is not None and choice.delta.content:
                self.output_pieces.append(choice.delta.content)

    def start_over(self) -> None:
        """A retry on another node: what the failed attempt produced is not billed."""
        self.output_pieces.clear()
        self.reported = None

    def settle(self, *, estimator: TokenEstimator, request: UpstreamChatRequest) -> CompletionUsage:
        """The node's own count when it gave a non-zero one; otherwise an estimate."""
        if self.reported is not None and self.reported.total_tokens:
            return self.reported
        prompt, completion, total = estimator.estimate(body=request.wire(), output_pieces=self.output_pieces)
        return CompletionUsage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)
