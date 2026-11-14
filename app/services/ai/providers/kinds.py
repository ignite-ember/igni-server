"""What a backend is, and which API it speaks."""

from __future__ import annotations

from enum import StrEnum


class Wire(StrEnum):
    """The API a backend speaks, and so the endpoint that serves its models."""

    #: `POST /v1/chat/completions`.
    OPENAI = 'openai'
    #: `POST /v1/messages`, Anthropic's Messages API: Claude, wherever it is served.
    ANTHROPIC = 'anthropic'


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
    #: Anthropic's own API, or an Anthropic-compatible gateway in the customer's network: a key.
    ANTHROPIC = 'anthropic'
    #: Claude on AWS Bedrock: requests signed (SigV4) with an IAM role or keys.
    BEDROCK = 'bedrock'
    #: Claude on Vertex AI: Google credentials.
    VERTEX_ANTHROPIC = 'vertex_anthropic'
    #: Claude in Microsoft Foundry: Entra ID, or the resource's key.
    AZURE_ANTHROPIC = 'azure_anthropic'

    @property
    def wire(self) -> Wire:
        """The one API this kind speaks."""
        return _WIRE[self]

    @property
    def is_cloud(self) -> bool:
        """A provider's service rather than the customer's own vLLM: no node gauges to scrape."""
        return self is not BackendKind.OPENAI

    @property
    def is_vertex(self) -> bool:
        """Addressed by a Google Cloud project and region."""
        return self in (BackendKind.VERTEX_OPENAI, BackendKind.VERTEX_ANTHROPIC)

    @property
    def derives_address(self) -> bool:
        """Its address is built from its region (and project), so moving those moves the address."""
        return self.has_region

    @property
    def has_region(self) -> bool:
        """Addressed by a region: Vertex AI, and Bedrock."""
        return self.is_vertex or self is BackendKind.BEDROCK


_WIRE: dict[BackendKind, Wire] = {
    BackendKind.OPENAI: Wire.OPENAI,
    BackendKind.AZURE_OPENAI: Wire.OPENAI,
    BackendKind.VERTEX_OPENAI: Wire.OPENAI,
    BackendKind.ANTHROPIC: Wire.ANTHROPIC,
    BackendKind.BEDROCK: Wire.ANTHROPIC,
    BackendKind.VERTEX_ANTHROPIC: Wire.ANTHROPIC,
    BackendKind.AZURE_ANTHROPIC: Wire.ANTHROPIC,
}
