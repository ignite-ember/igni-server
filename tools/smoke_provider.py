"""Send one real request through a configured backend, the way igni server would.

Proves, before users rely on it, the two things no test here can: that
the server's identity — its IAM role, an assumed role, Workload
Identity, a managed identity, a stored credential — is accepted by the
cloud, and that the model name reaches a model. It uses the server's own
code path (the backend as the router loads it, the lease the pool hands
out, the same signing, the same address), so a pass means a user's
request would pass.

Run it inside the API container, which has the deployment's
configuration, against a backend that already exists (static,
registered or added in the portal):

    python tools/smoke_provider.py bedrock-eu
    python tools/smoke_provider.py bedrock-eu --model claude-opus-5-5 --stream

This reaches outside on purpose — it is the one thing that does — and so
it is a hand-run tool, never part of the test suite or of CI. It sends
one short prompt, prints the status, the time taken and the provider's
token count, and never prints a credential or the reply's content.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import time

import httpx

# Run as a script from tools/, so the app package is beside it, not on the path.
_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.api.base_v1.schema.chat import ChatCompletionRequest  # noqa: E402
from app.api.base_v1.schema.messages import MessagesRequest  # noqa: E402
from app.services.ai.backend import BackendLease  # noqa: E402
from app.services.ai.backend_provider import BackendConfigError, InferenceBackendProvider  # noqa: E402
from app.services.ai.chat.schemas import UpstreamChatRequest  # noqa: E402
from app.services.ai.chat.upstream_client import ClientFactory, UpstreamChatClient  # noqa: E402
from app.services.ai.messages.calls import CallerHeaders  # noqa: E402
from app.services.ai.messages.events import ProviderMessage  # noqa: E402
from app.services.ai.messages.upstream import MessagesUpstream  # noqa: E402
from app.services.ai.messages.usage import MessagesUsage, StreamTally  # noqa: E402
from app.services.ai.providers.credentials import CloudCredentials  # noqa: E402
from app.services.ai.providers.kinds import Wire  # noqa: E402
from app.services.ai.upstream import UpstreamFailure  # noqa: E402

#: Short on purpose: this proves a path, it does not test a model.
PROMPT = 'Reply with the single word: ready'
#: Enough for the one word, and little enough that the call costs nothing.
MAX_TOKENS = 16


class Smoke:
    """One request, sent over the backend's own API with the server's own credentials."""

    def __init__(self, *, credentials: CloudCredentials, client_factory: ClientFactory) -> None:
        self._credentials = credentials
        self._client_factory = client_factory

    async def send(self, lease: BackendLease, *, wire: Wire, stream: bool) -> str:
        """What the provider counted, in a line."""
        if wire is Wire.ANTHROPIC:
            return await self._messages(lease, stream=stream)
        return await self._completions(lease, stream=stream)

    async def _completions(self, lease: BackendLease, *, stream: bool) -> str:
        body = ChatCompletionRequest(
            messages=[{'role': 'user', 'content': PROMPT}], max_tokens=MAX_TOKENS, stream=stream
        )
        request = UpstreamChatRequest.for_upstream(body, model=lease.model)
        client = UpstreamChatClient(credentials=self._credentials, client_factory=self._client_factory)
        if not stream:
            usage = (await client.complete(lease=lease, request=request)).usage
            return f'tokens: {usage.total_tokens if usage else "not reported"}'
        lines = 0
        async for _line in client.stream(lease=lease, request=request):
            lines += 1
        return f'{lines} stream lines'

    async def _messages(self, lease: BackendLease, *, stream: bool) -> str:
        body = MessagesRequest(
            model=lease.model, max_tokens=MAX_TOKENS, messages=[{'role': 'user', 'content': PROMPT}], stream=stream
        ).model_dump(exclude_none=True)
        upstream = MessagesUpstream(credentials=self._credentials, client_factory=self._client_factory)
        if not stream:
            message = await upstream.complete(lease=lease, body=body, asked=CallerHeaders())
            usage = MessagesUsage.of(ProviderMessage.model_validate(message).usage)
        else:
            tally = StreamTally()
            async for event in upstream.stream(lease=lease, body=body, asked=CallerHeaders()):
                if event.event is not None:
                    tally.absorb(event.event)
            usage = tally.usage
        return (
            f'tokens: input {usage.input_tokens} (cache read {usage.cache_read}, written {usage.cache_write}), '
            f'output {usage.output}'
        )


async def main(
    argv: list[str],
    *,
    credentials: CloudCredentials | None = None,
    client_factory: ClientFactory = httpx.AsyncClient,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('backend', help='the backend id, as the fleet view in the portal lists it')
    parser.add_argument('--model', help="the model to ask for; the backend's first by default")
    parser.add_argument('--stream', action='store_true', help='stream the answer, as igni clients do')
    args = parser.parse_args(argv)

    try:
        backends = {backend.id: backend for backend in await InferenceBackendProvider().load()}
    except BackendConfigError as error:
        print(error)
        return 2
    backend = backends.get(args.backend)
    if backend is None:
        print(f'No backend {args.backend!r}. This deployment has: {", ".join(sorted(backends)) or "none"}')
        return 2
    model = args.model or backend.primary_model
    if not backend.serves(model):
        print(f'Backend {backend.id!r} does not serve {model!r}; it serves {", ".join(backend.models)}')
        return 2

    lease = BackendLease.for_backend(backend, lease_id='smoke', model=model)
    sent_as = lease.upstream_model(model)
    print(f'{backend.id}: {backend.kind.value}, {lease.auth.value} auth, {lease.base_url}')
    print(
        f'asking for {model!r}'
        + (f' (sent as {sent_as!r})' if sent_as != model else '')
        + ('; streamed' if args.stream else '')
    )

    smoke = Smoke(credentials=credentials or CloudCredentials(), client_factory=client_factory)
    started = time.monotonic()
    try:
        outcome = await smoke.send(lease, wire=backend.wire, stream=args.stream)
    except UpstreamFailure as failure:
        print(f'FAILED after {time.monotonic() - started:.1f}s: {failure.answer_status} — {failure.sentence}')
        print("The server log has the provider's own words; a 401 or 403 is the identity, a 404 the model name.")
        return 1
    print(f'OK in {time.monotonic() - started:.1f}s — {outcome}')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main(sys.argv[1:])))
