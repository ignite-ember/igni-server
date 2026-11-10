"""OpenAI-compatible chat completion request/response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolCallFunction(BaseModel):
    """The function an assistant's tool call names. Extra fields are passed through."""

    model_config = ConfigDict(extra='allow')

    name: str | None = None
    #: A JSON string on the wire; some clients send the object itself.
    arguments: str | dict[str, Any] | None = None


class ToolCall(BaseModel):
    """One of an assistant message's `tool_calls`. Extra fields are passed through."""

    model_config = ConfigDict(extra='allow')

    id: str | None = None
    type: str | None = None
    function: ToolCallFunction | None = None


class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None
    reasoning_content: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat-completion request. Extra fields are passed through to the upstream provider."""

    model_config = {'extra': 'allow'}

    model: str | None = None  # when set, forwarded to upstream and used to filter the key pool
    #: A conversation, not a corpus. 10,000 turns is far past any real
    #: session; 200,000 was accepted before this bound and forwarded on.
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=10_000)
    temperature: float | None = None
    top_p: float | None = None
    #: The one field that multiplies *spend* rather than size, so the body
    #: limit does not cover it: ``n=100000`` was accepted and passed
    #: straight through to the provider. OpenAI's own ceiling is 128; this
    #: product uses 1, so 16 is generous and bounded.
    n: int | None = Field(default=None, ge=1, le=16)
    stream: bool = False
    stop: str | list[str] | None = None
    #: Providers clamp this to the model's own limit, so the bound is only
    #: here to refuse nonsense — ``max_tokens=10000000`` was accepted.
    #: Deliberately well above any current model rather than tracking them.
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    user: str | None = None
    tools: list | None = None
    tool_choice: str | dict | None = None
    response_format: dict | None = None


# --- Response (non-streaming) ---


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str | None = None


class CompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str = ''
    object: str = 'chat.completion'
    created: int = 0
    model: str = ''
    choices: list[ChatCompletionChoice] = Field(default_factory=list)
    usage: CompletionUsage | None = None


# --- Model discovery (GET /chat/models) ---


class ChatModelEntry(BaseModel):
    """One model the caller's effective key pool can serve.

    We deliberately do NOT return the upstream provider ``base_url``
    here — the CLI talks to igni Server's ``/chat/completions``
    endpoint, which proxies to whichever key+URL pair the pool picks.
    Leaking the upstream URL gives the client no useful information
    (it can't bypass the proxy) and exposes routing internals.
    """

    id: str


class ChatModelsResponse(BaseModel):
    models: list[ChatModelEntry]
