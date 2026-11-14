"""Send one real request through a configured backend, the way igni server would.

Proves, before users rely on it, the two things no test here can: that
the server's identity — its IAM role, an assumed role, Workload
Identity, a managed identity, a stored credential — is accepted by the
cloud, and that the model name reaches a model. It uses the server's own
code path (the backend as the router loads it, the same signing, the same
address), so a pass means a user's request would pass.

Run it where igni server runs, with its configuration, against a
backend that already exists (static, registered or added in the portal):

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
import sys
import time
from typing import Any

from app.api.base_v1.schema.chat import ChatCompletionRequest
from app.services.ai.backend_provider import InferenceBackendProvider
from app.services.ai.chat.schemas import UpstreamChatRequest
from app.services.ai.chat.upstream_client import UpstreamChatClient
from app.services.ai.messages.calls import CallerHeaders
from app.services.ai.messages.upstream import MessagesUpstream
from app.services.ai.messages.usage import MessagesUsage, StreamTally
from app.services.ai.providers.kinds import Wire
from app.services.ai.schemas import BackendLease, InferenceBackend
from app.services.ai.upstream import UpstreamFailure

#: Short on purpose: this proves a path, it does not test a model.
PROMPT = 'Reply with the single word: ready'


def lease_of(backend: InferenceBackend, model: str) -> BackendLease:
    """The lease the pool would hand out for *backend*, without taking a slot."""
    return BackendLease(
        lease_id='smoke',
        backend_id=backend.id,
        base_url=backend.base_url,
        model=model,
        api_key=backend.api_key,
        kind=backend.kind,
        region=backend.region,
        auth=backend.auth,
        auth_settings=dict(backend.auth_settings),
        credential=backend.credential,
        model_ids=dict(backend.model_ids),
    )


async def openai_wire(lease: BackendLease, *, stream: bool) -> str:
    body = ChatCompletionRequest(messages=[{'role': 'user', 'content': PROMPT}], max_tokens=16, stream=stream)
    request = UpstreamChatRequest.for_upstream(body, model=lease.model)
    client = UpstreamChatClient()
    if not stream:
        completion = await client.complete(lease=lease, request=request)
        usage = completion.usage
        return f'tokens: {usage.total_tokens if usage else "not reported"}'
    lines = 0
    async for _line in client.stream(lease=lease, request=request):
        lines += 1
    return f'{lines} stream lines'


async def anthropic_wire(lease: BackendLease, *, stream: bool) -> str:
    body: dict[str, Any] = {
        'model': lease.model,
        'max_tokens': 16,
        'messages': [{'role': 'user', 'content': PROMPT}],
        'stream': stream,
    }
    upstream = MessagesUpstream()
    if not stream:
        message = await upstream.complete(lease=lease, body=body, asked=CallerHeaders())
        usage = MessagesUsage.of_message(message)
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


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('backend', help='the backend id, as the fleet view in the portal lists it')
    parser.add_argument('--model', help="the model to ask for; the backend's first by default")
    parser.add_argument('--stream', action='store_true', help='stream the answer, as igni clients do')
    args = parser.parse_args(argv)

    backends = {backend.id: backend for backend in await InferenceBackendProvider().load()}
    backend = backends.get(args.backend)
    if backend is None:
        print(f'No backend {args.backend!r}. This deployment has: {", ".join(sorted(backends)) or "none"}')
        return 2
    model = args.model or backend.primary_model
    if not backend.serves(model):
        print(f'Backend {backend.id!r} does not serve {model!r}; it serves {", ".join(backend.models)}')
        return 2

    lease = lease_of(backend, model)
    sent_as = lease.upstream_model(model)
    print(f'{backend.id}: {backend.kind.value}, {backend.auth.value if backend.auth else "-"} auth, {backend.base_url}')
    print(
        f'asking for {model!r}'
        + (f' (sent as {sent_as!r})' if sent_as != model else '')
        + ('; streamed' if args.stream else '')
    )

    started = time.monotonic()
    try:
        if backend.wire is Wire.ANTHROPIC:
            outcome = await anthropic_wire(lease, stream=args.stream)
        else:
            outcome = await openai_wire(lease, stream=args.stream)
    except UpstreamFailure as failure:
        print(f'FAILED after {time.monotonic() - started:.1f}s: {failure.answer_status} — {failure.sentence}')
        print("The server log has the provider's own words; a 401 or 403 is the identity, a 404 the model name.")
        return 1
    print(f'OK in {time.monotonic() - started:.1f}s — {outcome}')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main(sys.argv[1:])))
