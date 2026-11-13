"""What a backend is, and which API it speaks."""

from __future__ import annotations

from enum import StrEnum


class Wire(StrEnum):
    """The API a backend speaks, and so the endpoint that serves its models."""

    #: `POST /v1/chat/completions`.
    OPENAI = 'openai'


class BackendKind(StrEnum):
    """Who serves a backend, which decides its address and its credentials.

    `openai` is every backend there was before kinds: the customer's vLLM
    nodes, OpenAI itself, anything OpenAI-compatible, with an optional
    Bearer key. It is the default, so a configuration written before kinds
    existed means what it always meant.
    """

    OPENAI = 'openai'
    #: Azure OpenAI's v1 API: Managed Identity, a client secret, or the resource's key.
    AZURE_OPENAI = 'azure_openai'
    #: Vertex AI's OpenAI-compatible endpoint (Gemini, open models): Google credentials.
    VERTEX_OPENAI = 'vertex_openai'

    @property
    def wire(self) -> Wire:
        """The one API this kind speaks."""
        return _WIRE[self]

    @property
    def is_cloud(self) -> bool:
        """Signed with a cloud's own credentials rather than a static key."""
        return self is not BackendKind.OPENAI


_WIRE: dict[BackendKind, Wire] = {
    BackendKind.OPENAI: Wire.OPENAI,
    BackendKind.AZURE_OPENAI: Wire.OPENAI,
    BackendKind.VERTEX_OPENAI: Wire.OPENAI,
}
