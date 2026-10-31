"""OpenAI-compatible /chat/completions — proxied through the AI key pool.

Authenticates the caller, resolves their org membership, leases a
backend from the pool, and forwards the request upstream. Both
streaming (SSE) and non-streaming responses.

**A request that cannot get a backend is refused, not queued.** This
docstring used to describe the opposite — "the response streams
keepalive comments until a slot opens up" — and no such mechanism
exists or ever did: `_answer_for` turns `NoBackendAvailableError` into
a 429 with `Retry-After`. The keepalives are real but unrelated; they
pace a stream that is already running (`sse_keepalive`).

The parts that are not routing live beside it: provider payload
repair in `services/ai/provider_quirks`, token estimation in
`services/ai/token_estimate`, stream pacing in `services/ai/sse_keepalive`.
"""

import asyncio
from dataclasses import dataclass
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordBearer
import httpx

from app.api.base_v1.endpoints.client.openai_errors import error_envelope, OpenAICompatibleRoute, OpenAIErrorBody
from app.api.base_v1.schema.chat import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ChatModelEntry,
    ChatModelsResponse,
    CompletionUsage,
)
from app.config import settings
from app.db.sql.chat_usage import ChatUsageDB
from app.security.producer import Producers
from app.services.ai.backend_pool import InferenceBackendPool
from app.services.ai.fair_queue import FairQueue, QueueFull, QueueTimeout
from app.services.ai.pool_manager import PoolManager
from app.services.ai.provider_quirks import (
    normalise_choices,
    sanitise_message_sequence,
    sanitise_tool_call_arguments,
)
from app.services.ai.request_shape import describe as describe_chat_request
from app.services.ai.schemas import (
    BackendLease,
    ModelNotAvailableError,
    NoBackendConfiguredError,
)
from app.services.ai.sse_keepalive import lines_with_keepalive
from app.services.ai.token_estimate import TokenEstimator
from app.services.ai.upstream import (
    describe as _describe_upstream,
    MAX_RETRIES as _MAX_RETRIES,
    RETRY_BACKOFF as _RETRY_BACKOFF,
    UpstreamError as _UpstreamError,
    UpstreamStale as _UpstreamStale,
    UpstreamUnreadable as _UpstreamUnreadable,
    worth_another_node as _worth_another_node,
)
from app.services.auth.auth_service import AuthService
from app.services.portal.groups_service import GroupsService
from app.utils.cache import async_ttl_cache

logger = logging.getLogger(__name__)

# Every refusal on these routes — including the ones raised while
# resolving the token — comes back in the envelope an OpenAI SDK reads.
# Without it this server's accurate 502 reached the screen as "Unknown
# model error". See `openai_errors`.
router = APIRouter(route_class=OpenAICompatibleRoute)

_oauth2 = OAuth2PasswordBearer(tokenUrl='token')
_usage_db = ChatUsageDB()


@dataclass
class _RequestContext:
    """One request's identity, its model, and the node serving it."""

    user_id: str
    org_id: str | None
    model: str
    # The lease is held for the whole request — including the tail of a
    # stream — and released in a ``finally`` on every exit path. Holding
    # the lease rather than a backend id is what keeps the node's
    # concurrency gauge correct when the same user has two requests in
    # flight.
    lease: BackendLease
    pool: InferenceBackendPool
    #: An app's own id, or a space's app; None for a person.
    app_id: str | None = None

    def switch_to(self, lease: BackendLease, body: dict) -> None:
        """Point this request at a different node.

        Three things have to move together — the lease, the model name
        used for usage accounting, and the model in the body actually
        sent upstream — and they were being assigned one at a time on
        two separate retry paths. A node serving a different model than
        the one billed is what forgetting the third line produces.
        """
        self.lease = lease
        self.model = lease.model
        body['model'] = lease.model

    async def release(self) -> None:
        """Hand the node's concurrency slot back, however this ended.

        Idempotent by construction: the pool removes the lease from a
        Redis sorted set, and removing a member that is not there is a
        no-op. That matters because the streaming retry path releases
        and re-acquires mid-flight, so this may run against a lease
        already returned.

        A failure here must never mask the outcome of the request. The
        pool does prune leases older than ``lease_timeout``, so a missed
        release heals — but "eventually" is 900 seconds of a node
        looking one-busier than it is, and the pool's own documentation
        says relying on that sweep is wrong precisely when scheduling
        matters.
        """
        try:
            await self.pool.release(self.lease)
        except Exception as exc:  # noqa: BLE001 — never mask the response
            logger.warning('Failed to release inference backend: %s', exc)
        # A slot came free: the next in turn takes it.
        FairQueue.of_loop(max_length=settings.chat_queue_max_length).kick()

    async def log_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        duration: float,
        stream: bool,
    ) -> None:
        """Persist usage.

        This used to release the lease as well, on the reasoning that it
        runs when the response is fully delivered. True on the happy
        path, and the problem: it is only *reached* on the happy path. A
        client that disconnected mid-stream returned from the generator
        before this, and a non-200 upstream raised before it — so the
        lease was never handed back. Releasing lives in a ``finally`` on
        each path now.
        """
        try:
            await _usage_db.log(
                user_id=self.user_id,
                org_id=self.org_id,
                app_id=self.app_id,
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                duration=round(duration, 3),
                stream=stream,
            )
        except Exception as exc:  # noqa: BLE001 — usage is not the response
            logger.warning('Failed to log chat usage: %s', exc)


def _error_frame(message: str, status_code: int) -> str:
    """One SSE frame carrying the same envelope an HTTP refusal uses.

    The transport differs; the API does not. This frame used to be built
    inline with `"type": "upstream_error"`, which is not a type any
    OpenAI SDK recognises, so a client that knew how to read a refusal
    on the non-streaming path could not read this one.
    """
    envelope = OpenAIErrorBody(error=error_envelope(message=message, status_code=status_code))
    # `exclude={'detail'}`, not `exclude_none`. `detail` is absent on
    # this path and sending it as null would be a field the SDK did
    # not see before — but `code` and `param` are null *deliberately*
    # ("null rather than invented"), and an SDK reads them. Dropping
    # every null drops those two as well, which is what the first
    # version of this line did.
    frame = envelope.model_dump(exclude={'detail'})
    return f'data: {json.dumps(frame)}\n\n'


def _auth_headers(lease: BackendLease) -> dict[str, str]:
    """Headers for an upstream call.

    ``Authorization`` is omitted entirely when the backend has no token.
    A vLLM node on a private network normally has no auth, and sending
    ``Bearer None`` would be a confusing 401 rather than a clean call.
    """
    headers = {'Content-Type': 'application/json'}
    if lease.api_key:
        headers['Authorization'] = f'Bearer {lease.api_key}'
    return headers


# ---------------------------------------------------------------------------
# Normalisation helpers — shared between streaming and non-streaming
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@async_ttl_cache(maxsize=10_000, ttl=60)
async def _resolve_models_for() -> list[tuple[str, str]]:
    """Cached lookup of the (model, base_url) pairs this deployment serves.

    Deliberately not per-org, and it used to say otherwise. The signature
    was ``_resolve_models_for(org_id)``, the docstring said "available
    for this caller", and the endpoint below advertised "org override or
    global pool" — while the body called
    ``pool.list_available_models('chat')`` and ignored the argument
    entirely. There is no per-org model override anywhere in this
    codebase: pools are global per type.

    So three things read as though access were scoped when it was not,
    and the cache held up to 10,000 identical entries keyed on an
    argument that changed nothing. The parameter is gone rather than
    wired up, because a per-org model allowlist is a feature nobody has
    asked for and inventing one to justify a signature is the wrong way
    round. F111.
    """
    pool = PoolManager().get_chat_pool()
    pairs: list[tuple[str, str]] = await pool.list_available_models('chat')
    return pairs


@router.get(
    '/chat/models',
    response_model=ChatModelsResponse,
    status_code=status.HTTP_200_OK,
    description='List the model identifiers this deployment serves. Not per-org: there is no per-org override.',
)
async def list_chat_models(
    request: Request,
    token: str = Depends(_oauth2),
) -> ChatModelsResponse:
    header_org_id = request.headers.get('x-org-id')
    # The org context is still resolved, and still asserts active
    # membership — that is what makes this endpoint require a *current*
    # member rather than anybody with a token. It just does not filter
    # the result, because nothing is per-org to filter by.
    await AuthService.resolve_user_org(token, org_id=header_org_id)
    pairs = await _resolve_models_for()
    # Dedupe model IDs — the same model may be served by multiple
    # keys (same or different upstream URLs); the CLI only needs the
    # set of available IDs since every call goes through this proxy.
    seen: set[str] = set()
    entries: list[ChatModelEntry] = []
    for model_id, _base_url in pairs:
        if model_id in seen:
            continue
        seen.add(model_id)
        entries.append(ChatModelEntry(id=model_id))
    return ChatModelsResponse(models=entries)


@router.post(
    '/chat/completions',
    response_model=ChatCompletionResponse,
    status_code=status.HTTP_200_OK,
    description='OpenAI-compatible chat completion (proxied via AI key pool)',
    responses={429: {'description': 'All keys for this pool are at capacity'}},
)
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    token: str = Depends(_oauth2),
):
    header_org_id = request.headers.get('x-org-id')
    # An app's or a space's VM runs agents too (docs/service-identity.md
    # §11, §17): its own token is accepted, and its usage is the
    # organisation's, recorded as `app:<id>` / `space:<id>` with the app.
    producer = await Producers().machine(request, token)
    if producer is not None:
        user_id, org_id = f'{producer.kind}:{producer.id}', producer.org_id
        app_id = producer.app_id or producer.id
    else:
        user_id, org_id = await AuthService.resolve_user_org(token, org_id=header_org_id)
        app_id = None

    pool = PoolManager().get_chat_pool()
    # An explicit choice wins; otherwise the group's, if it pinned one.
    # A legal team routed to a legal-review adapter should not have to
    # remember its name, and should not silently get the general model
    # when they forget.
    requested_model = body.model or (None if producer is not None else await GroupsService.model_for_user(user_id))

    async def acquire(who: str, model: str | None) -> BackendLease:
        return await pool.acquire(who, 'chat', model=model)

    # Every node full: wait in turn — fairly between organisations, then
    # producers — rather than being refused (docs/service-identity.md §19).
    queue = FairQueue.of_loop(max_length=settings.chat_queue_max_length)
    try:
        ai = await queue.take(
            org=org_id or '',
            producer=user_id,
            user_id=user_id,
            model=requested_model,
            acquire=acquire,
            release=pool.release,
            max_wait=settings.chat_queue_max_wait_seconds,
            gone=request.is_disconnected,
        )
    except ModelNotAvailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except NoBackendConfiguredError as exc:
        # A deployment problem, not a load problem — retrying will not
        # help, so say so with a 503 rather than a 429 with Retry-After.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except QueueFull:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail='Every inference node is at capacity and the queue is full. Please retry shortly.',
            headers={'Retry-After': '30'},
        )
    except QueueTimeout:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail='Waited the longest a request may for a free inference node. Please retry.',
            headers={'Retry-After': '30'},
        )

    # Honour the caller's model choice when set; fall back to whatever
    # model the node advertises only when the caller didn't pick one.
    chosen_model = requested_model or ai.model

    upstream_body = body.model_dump(exclude_none=True)
    upstream_body['model'] = chosen_model
    sanitise_tool_call_arguments(upstream_body)
    sanitise_message_sequence(upstream_body)
    if body.stream:
        upstream_body.setdefault('stream_options', {'include_usage': True})

    upstream_headers = _auth_headers(ai)
    url = f'{ai.base_url.rstrip("/")}/chat/completions'
    ctx = _RequestContext(
        user_id=user_id,
        org_id=org_id,
        app_id=app_id,
        model=chosen_model,
        lease=ai,
        pool=pool,
    )

    if body.stream:
        return await _stream_response(url, upstream_headers, upstream_body, ctx, request)
    return await _non_stream_response(url, upstream_headers, upstream_body, ctx)


# ---------------------------------------------------------------------------
# When a node's answer is worth asking a different node for
# ---------------------------------------------------------------------------


def _answer_for(exc: Exception) -> HTTPException:
    """The shared description of a failure, as an HTTP refusal.

    The sentence and the status come from `upstream.describe`. Only the
    429's `Retry-After` is added here: a header is an HTTP fact, and the
    background caller that shares this vocabulary has nowhere to put one.
    """
    status_code, detail = _describe_upstream(exc)
    headers = {'Retry-After': '30'} if status_code == status.HTTP_429_TOO_MANY_REQUESTS else None
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


# ---------------------------------------------------------------------------
# Non-streaming
# ---------------------------------------------------------------------------


async def _non_stream_response(
    url: str,
    headers: dict,
    body: dict,
    ctx: _RequestContext,
) -> ChatCompletionResponse:
    """One completion, retried on a different node if the first is gone.

    The streaming path has had this since it was written; this one had
    neither the retry nor any handling of a transport failure, so a
    registered-but-unreachable node produced `httpx.ConnectError`
    escaping the handler as a bare **500**. Found by registering a node
    and pointing it at an address nothing was listening on — which is
    what a spot reclaim, a crash, or a mistyped `base_url` looks like,
    and the registry keeps a dead node for up to its 180-second TTL.

    A 500 is also the wrong thing to tell the caller: it blames this
    server for the upstream being absent, and it trips the
    `IgniServerErrors` alert (F101) for a fault that is not ours. F125.

    **A node that answers badly is retried too**, which it was not
    before: only a transport failure moved on to another node, so a
    node that was up and unwell — a vLLM still loading its weights
    answers 503 — failed the whole request on the first reply while
    healthy nodes sat idle beside it. The streaming path had retried
    that case since it was written; this one gave up. The two now agree
    on what is worth another node, in one place: `_worth_another_node`.
    """
    t0 = time.monotonic()
    failed_backend_id: str | None = None

    try:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return await _non_stream_inner(url, headers, body, ctx, t0)
            except (httpx.RequestError, _UpstreamError, _UpstreamUnreadable) as exc:
                # Three shapes of the same question — did this node
                # produce a usable answer. No reply at all, a reply that
                # is an error, and a 200 carrying something unparseable.
                failed_backend_id = ctx.lease.backend_id
                logger.warning(
                    'Node %s did not answer usefully (%s): %s',
                    failed_backend_id,
                    type(exc).__name__,
                    exc,
                )
                # Out of attempts, or nothing another node would fix.
                if attempt == _MAX_RETRIES or not _worth_another_node(exc):
                    raise _answer_for(exc) from exc

                try:
                    # Hand the dead node's slot back before taking
                    # another, or a retry loop inflates its gauge — the
                    # same reason the streaming path releases first.
                    await ctx.pool.release(ctx.lease)
                    ai = await ctx.pool.acquire(
                        ctx.user_id, 'chat', exclude_backend_id=failed_backend_id, model=ctx.model
                    )
                except Exception as acquire_exc:  # noqa: BLE001 — nothing left to try
                    # The original failure is the one worth reporting:
                    # it says what went wrong, where "could not find
                    # another node" only says we stopped looking.
                    raise _answer_for(exc) from acquire_exc

                await asyncio.sleep(_RETRY_BACKOFF * attempt)
                url = f'{ai.base_url.rstrip("/")}/chat/completions'
                headers = _auth_headers(ai)
                ctx.switch_to(ai, body)

        # Unreachable: the loop either returns or raises.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='No inference node answered. Try again shortly.',
        )
    finally:
        # Covers the 502 branches, which raise before usage is logged
        # and so used to leave the lease outstanding for the full 900s
        # lease timeout.
        await ctx.release()


async def _non_stream_inner(
    url: str,
    headers: dict,
    body: dict,
    ctx: _RequestContext,
    t0: float,
) -> ChatCompletionResponse:
    async with httpx.AsyncClient(timeout=settings.ai.upstream_timeout) as client:
        resp = await client.post(url, headers=headers, json=body)

    if resp.status_code != 200:
        logger.error('Upstream AI error %s: %s', resp.status_code, resp.text[:500])
        # Raised rather than answered, so the caller above can decide
        # whether another node is worth trying. It used to become a 502
        # here, which ended the request on the first bad reply.
        raise _UpstreamError(resp.status_code, resp.text[:500])

    try:
        data = resp.json()
    except ValueError as exc:
        # A 200 carrying something that is not JSON. This raised
        # JSONDecodeError and became a bare 500, blaming this server for
        # the upstream's reply. Found with a stub node that streamed SSE
        # to a non-streaming request — a real node behind a
        # misconfigured proxy does the same, and another node answers.
        logger.error(
            'Upstream returned %s with an unparseable body: %r',
            resp.status_code,
            resp.text[:200],
        )
        raise _UpstreamUnreadable(resp.text[:200]) from exc

    normalise_choices(data, key='message')

    usage = data.get('usage') or {}
    await ctx.log_usage(
        input_tokens=usage.get('prompt_tokens', 0),
        output_tokens=usage.get('completion_tokens', 0),
        total_tokens=usage.get('total_tokens', 0),
        duration=time.monotonic() - t0,
        stream=False,
    )

    return ChatCompletionResponse(
        id=data.get('id', ''),
        object=data.get('object', 'chat.completion'),
        created=data.get('created', 0),
        model=data.get('model', ''),
        choices=[
            ChatCompletionChoice(
                index=c.get('index', i),
                message=ChatMessage(**c['message']),
                finish_reason=c.get('finish_reason'),
            )
            for i, c in enumerate(data.get('choices', []))
        ],
        usage=CompletionUsage(**usage) if usage else None,
    )


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


async def _stream_upstream(url: str, headers: dict, body: dict):
    """Make a single streaming request to the upstream and yield (sse_line, parsed_chunk).

    Raises ``_UpstreamStale`` if the stream goes silent for too long.
    ``parsed_chunk`` is the parsed JSON dict for data lines, or None for comments/done.
    """
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream('POST', url, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                error_body = await resp.aread()
                logger.error('Upstream AI stream error %s: %s', resp.status_code, error_body[:500])
                if resp.status_code == 400:
                    # The shape, never the body. This line used to be
                    # `json.dumps(body)` under a message claiming "first
                    # 500 chars" that it did not do — and the body of a
                    # chat completion is the prompt: the question, the
                    # source the CLI attached, the previous tool output.
                    # A 400 from vLLM is nearly always a malformed
                    # message sequence or a context length, and the
                    # shape answers both. F111.
                    logger.error('Upstream 400 — request %s', describe_chat_request(body))
                raise _UpstreamError(resp.status_code, error_body)

            async for line in lines_with_keepalive(resp):
                if not line:
                    continue

                # SSE comment keepalives — forward as-is
                if line.startswith(':'):
                    yield f'{line}\n\n', None
                    continue

                if not line.startswith('data: ') or line == 'data: [DONE]':
                    yield f'{line}\n\n', None
                    continue

                try:
                    chunk = json.loads(line[6:])
                except (json.JSONDecodeError, KeyError):
                    yield f'{line}\n\n', None
                    continue

                normalise_choices(chunk, key='delta')
                yield f'data: {json.dumps(chunk)}\n\n', chunk


async def _stream_response(
    url: str,
    headers: dict,
    body: dict,
    ctx: _RequestContext,
    request: Request,
) -> StreamingResponse:
    body['stream'] = True

    async def _generate():
        try:
            async for piece in _generate_inner():
                yield piece
        finally:
            # Reached on every exit: a completed stream, a client that
            # disconnected mid-stream, or an exception. The disconnect case
            # is the one that mattered — measured at 9 leaks from 9
            # cancelled streams, each held for 900s.
            await ctx.release()

    async def _generate_inner():
        t0 = time.monotonic()

        input_tokens = output_tokens = total_tokens = 0
        output_pieces: list[str] = []

        _last_disconnect_check = time.monotonic()
        current_url = url
        current_headers = headers
        failed_backend_id: str | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            # On retries, re-acquire a different key from the pool
            if attempt > 1:
                try:
                    # Hand the failed node's slot back before taking
                    # another, or a retry loop inflates its gauge.
                    await ctx.pool.release(ctx.lease)
                    ai = await ctx.pool.acquire(
                        ctx.user_id, 'chat', exclude_backend_id=failed_backend_id, model=ctx.model
                    )
                    current_url = f'{ai.base_url.rstrip("/")}/chat/completions'
                    current_headers = _auth_headers(ai)
                    ctx.switch_to(ai, body)
                except Exception as exc:  # noqa: BLE001 — retry on the old node
                    # Carries on against the node that just failed, which
                    # is the right fallback and a bad silence: a pool
                    # refusing every acquire produced no line at all, so
                    # a deployment retrying forever against one sick node
                    # looked identical to one with nothing wrong.
                    logger.warning(
                        'Could not take another node after %s failed; retrying on it: %s',
                        failed_backend_id,
                        exc,
                    )

            try:
                async for line, chunk_data in _stream_upstream(current_url, current_headers, body):
                    now = time.monotonic()
                    if now - _last_disconnect_check >= 2.0:
                        _last_disconnect_check = now
                        if await request.is_disconnected():
                            logger.info('Client disconnected during stream')
                            return
                    if not chunk_data:
                        yield line
                        continue
                    u = chunk_data.get('usage')
                    if u:
                        total_tokens = u.get('total_tokens', 0)
                        input_tokens = u.get('prompt_tokens', 0)
                        output_tokens = u.get('completion_tokens', 0)
                    for choice in chunk_data.get('choices', []):
                        piece = choice.get('delta', {}).get('content')
                        if piece:
                            output_pieces.append(piece)
                    yield line
                break  # success — exit retry loop

            except (_UpstreamStale, _UpstreamError, httpx.ReadTimeout) as exc:
                failed_backend_id = ctx.lease.backend_id
                if isinstance(exc, _UpstreamStale):
                    label = 'stale'
                elif isinstance(exc, _UpstreamError):
                    label = f'{exc.status_code}'
                else:
                    label = 'read_timeout'

                # A 4xx that is not 408 or 429 is about the request, and
                # the request is the same on every node. Retrying it
                # spent two more nodes and the caller's patience to
                # arrive at the same refusal; now it stops here.
                if attempt < _MAX_RETRIES and _worth_another_node(exc):
                    logger.warning(
                        'Upstream %s on attempt %d/%d for %s (node=%s), retrying...',
                        label,
                        attempt,
                        _MAX_RETRIES,
                        ctx.model,
                        ctx.lease.backend_id,
                    )
                    yield ': retry\n\n'
                    output_pieces.clear()
                    input_tokens = output_tokens = total_tokens = 0
                    await asyncio.sleep(_RETRY_BACKOFF * attempt)
                else:
                    logger.error('Upstream %s, giving up after attempt %d', label, attempt)
                    # The same envelope the non-streaming path answers
                    # with, and the same sentence — an SSE frame is a
                    # different transport, not a different API. It said
                    # `"type": "upstream_error"`, which is not a type any
                    # OpenAI SDK knows, and one generic message for all
                    # three failures.
                    refusal = _answer_for(exc)
                    yield _error_frame(str(refusal.detail), refusal.status_code)
                    break

        if not total_tokens:
            input_tokens, output_tokens, total_tokens = TokenEstimator().estimate(
                body=body, output_pieces=output_pieces
            )

        await ctx.log_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            duration=time.monotonic() - t0,
            stream=True,
        )

    return StreamingResponse(
        _generate(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )
