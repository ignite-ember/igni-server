"""Is this node actually answering?

Heartbeats only tell us about nodes that send them. A node from the
static config has never checked in, and a node an admin pinned from the
portal has nothing to check in on its behalf — so before this, two of the
three sources appeared on the page with no liveness information at all,
and a machine that had been switched off looked identical to one serving
traffic.

So we ask the node directly. Every backend gets the same treatment
regardless of where it came from, and the answer is one of three things
an operator can act on:

* **healthy** — it answered.
* **degraded** — it answered, but something is off: at capacity, or a
  registered node whose heartbeats have stopped even though the endpoint
  still responds.
* **unreachable** — it did not answer. The address is wrong, the machine
  is off, or the network is in the way.

"We have not looked yet" is deliberately *not* a fourth colour on the
page. It is reported as ``unknown`` and rendered as degraded, because an
operator asking "is my cluster up?" is not helped by grey.

## Why probing is on-demand and cached

There is no scheduler in this deployment, and adding one to poll GPUs
would be a lot of machinery for a page that is only open when somebody is
looking at it. Instead the portal's request triggers the probe, and the
result is cached in Redis for a few seconds so that several admins with
the page open — it refreshes every ten seconds — share one probe per node
rather than each generating their own.

## What a probe is

``GET {base_url}/models``, which every OpenAI-compatible server answers
including vLLM. Deliberately not a completion: a real inference request
costs GPU time, and would report a busy node as slow rather than as busy.

A cloud backend is asked nothing at all: its probe is whether the server
can get a token for it. That is the part an operator controls — the role,
the identity, the stored key — and a model listing would call the provider
for every portal refresh to learn little the first request does not.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
import redis.asyncio as aioredis

from app.config import settings
from app.db.redis.keys import key as redis_key
from app.services.ai import metrics as node_metrics
from app.services.ai.providers.auth import AuthMethod
from app.services.ai.providers.credentials import CloudCredentials, CredentialUnavailable, shared_credentials, Signer
from app.services.ai.schemas import HealthState, InferenceBackend, NodeHealth, NodeMetrics, ProbedNode

logger = logging.getLogger(__name__)

# Long enough that a loaded node answering slowly is not called dead,
# short enough that a page render does not hang on a black-holed address.
_PROBE_TIMEOUT_SECONDS = 3.0

# How long a probe result is reused. Roughly the portal's refresh
# interval, so an open page mostly reads cache and a node coming back up
# is visible within a refresh or two.
_CACHE_TTL_SECONDS = 15

# A registered node whose heartbeats stopped this long ago is degraded
# even if it still answers a probe — something is wrong with whatever was
# supposed to be checking in for it.
_HEARTBEAT_GRACE_SECONDS = 180

# Named because two places test for it: the health verdict shows it, and
# discovery turns it into "you need to enter a token".
_REJECTED_CREDENTIALS = 'The node rejected our credentials'


#: How the health detail names each token method, for the person reading it.
_METHOD_NAMES = {
    AuthMethod.AMBIENT: "the server's own cloud identity",
    AuthMethod.MANAGED_IDENTITY: 'the user-assigned managed identity',
    AuthMethod.CLIENT_SECRET: "the app registration's client secret",
    AuthMethod.IMPERSONATE: 'the impersonated service account',
    AuthMethod.SERVICE_ACCOUNT_KEY: 'the stored service-account key',
}


class NodeHealthProbe:
    """Asks nodes whether they are answering, and remembers briefly."""

    def __init__(self, *, timeout: float = _PROBE_TIMEOUT_SECONDS, credentials: CloudCredentials | None = None) -> None:
        self._timeout = timeout
        self._credentials = credentials if credentials is not None else shared_credentials()
        # One client per loop, for the reason `NodeRegistry` documents:
        # Redis clients are loop-bound.
        self._redis_by_loop: dict[int, aioredis.Redis] = {}

    async def _get_redis(self) -> aioredis.Redis:
        loop_id = id(asyncio.get_running_loop())
        client = self._redis_by_loop.get(loop_id)
        if client is None:
            client = aioredis.from_url(settings.redis_url, decode_responses=True)
            self._redis_by_loop[loop_id] = client
        return client

    async def metrics_for(self, backends: list[InferenceBackend]) -> dict[str, NodeMetrics]:
        """Scrape every node's own gauges, concurrently.

        Separate from ``check_all`` because the two answer different
        questions and fail independently: a node can be perfectly
        reachable and publish no metrics, which is not a health problem.
        """
        # A cloud provider publishes no vLLM gauges.
        own = [b for b in backends if not b.kind.is_cloud]
        results = await asyncio.gather(
            *(node_metrics.fetch(base_url=b.base_url, api_key=b.api_key, timeout=self._timeout) for b in own),
            return_exceptions=True,
        )
        return {backend.id: result for backend, result in zip(own, results) if isinstance(result, NodeMetrics)}

    async def check_all(self, backends: list[InferenceBackend]) -> dict[str, NodeHealth]:
        """Probe every backend concurrently, reusing cached answers.

        Concurrent because the alternative is a page render that takes
        the sum of every node's latency, and one unreachable address
        would make the whole cluster feel broken.
        """
        results = await asyncio.gather(
            *(self.check(backend) for backend in backends),
            return_exceptions=True,
        )
        health: dict[str, NodeHealth] = {}
        for backend, result in zip(backends, results):
            if isinstance(result, BaseException):
                # A probe that fails to even run is not evidence about the
                # node, so it is reported as unknown rather than as down.
                logger.warning('Health probe for %s could not run: %s', backend.id, result)
                health[backend.id] = NodeHealth(state=HealthState.UNKNOWN, detail='The probe could not be run')
            else:
                health[backend.id] = result
        return health

    async def list_models(self, *, base_url: str, api_key: str | None = None) -> ProbedNode:
        """Ask an address what it serves, before it is a configured node.

        The same call as a health probe, kept and parsed rather than
        discarded. A vLLM process started with ``--enable-lora`` lists its
        base model and every adapter here, which is the only practical
        way to learn adapter names: they are whatever the operator passed
        to ``--lora-modules``.
        """
        health, payload = await self._request(base_url=base_url, api_key=api_key)
        return ProbedNode(
            health=health,
            models=_model_ids(payload),
            needs_credentials=health.detail == _REJECTED_CREDENTIALS,
        )

    async def check(self, backend: InferenceBackend) -> NodeHealth:
        cached = await self._cached(backend.id)
        if cached is not None:
            return cached

        health = await self._probe(backend)
        await self._remember(backend.id, health)
        return health

    # ── Internals ────────────────────────────────────────────────────

    async def _probe(self, backend: InferenceBackend) -> NodeHealth:
        if backend.kind.is_cloud:
            return await self._cloud_probe(backend)
        health, payload = await self._request(base_url=backend.base_url, api_key=backend.api_key)
        if health.state is not HealthState.HEALTHY:
            return health

        # It answered, but does it serve what we route to it? A node
        # configured for a model it does not have is never selected —
        # requests for that model report "not served by this deployment"
        # while the node sits on the page looking perfectly fine.
        served = set(_model_ids(payload))
        missing = [name for name in backend.models if name not in served]
        if served and missing:
            return health.model_copy(
                update={
                    'state': HealthState.DEGRADED,
                    'detail': f'Does not serve {", ".join(missing)}; it offers {", ".join(sorted(served))}',
                }
            )
        return health

    async def _cloud_probe(self, backend: InferenceBackend) -> NodeHealth:
        """Whether the server can sign for this cloud backend; the provider itself is not called."""
        if backend.auth is None or not backend.auth.is_token:
            # A static key (an Azure resource's) is checked by the first request, not here.
            return NodeHealth(state=HealthState.HEALTHY, detail='Uses a stored key; checked on its first request')
        started = time.monotonic()
        signer = Signer(
            backend_id=backend.id,
            kind=backend.kind,
            auth=backend.auth,
            settings=backend.auth_settings,
            secret=backend.credential,
        )
        try:
            await self._credentials.bearer(signer)
        except CredentialUnavailable:
            return NodeHealth(
                state=HealthState.DEGRADED,
                detail=f'Could not get a token with {_METHOD_NAMES.get(backend.auth, backend.auth.value)}',
            )
        return NodeHealth(
            state=HealthState.HEALTHY,
            detail='The server holds a token for this provider',
            latency_ms=(time.monotonic() - started) * 1000,
        )

    async def _request(self, *, base_url: str, api_key: str | None) -> tuple[NodeHealth, object]:
        """One GET against ``/models``, turned into a health verdict.

        Returns the parsed body alongside, because both callers want it:
        discovery lists the models, and the health check compares them to
        what the node is configured for.
        """
        headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
        url = f'{base_url.rstrip("/")}/models'
        started = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            return (
                NodeHealth(
                    state=HealthState.UNREACHABLE,
                    detail=f'No response within {self._timeout:g}s',
                ),
                None,
            )
        except httpx.HTTPError as exc:
            # Wrong address, DNS failure, refused connection, TLS
            # mismatch — all the same to an operator: it is not there.
            return NodeHealth(state=HealthState.UNREACHABLE, detail=_short(exc)), None

        latency_ms = (time.monotonic() - started) * 1000

        if response.status_code == 401 or response.status_code == 403:
            # It answered, so the address is right; the credential is not.
            # Calling that unreachable would send somebody to check
            # cabling instead of the token.
            return (
                NodeHealth(
                    state=HealthState.DEGRADED,
                    detail='The node rejected our credentials',
                    latency_ms=latency_ms,
                ),
                None,
            )
        if response.status_code >= 500:
            return (
                NodeHealth(
                    state=HealthState.UNREACHABLE,
                    detail=f'The node returned {response.status_code}',
                    latency_ms=latency_ms,
                ),
                None,
            )
        if response.status_code >= 400:
            return (
                NodeHealth(
                    state=HealthState.DEGRADED,
                    detail=f'The node returned {response.status_code}',
                    latency_ms=latency_ms,
                ),
                None,
            )

        try:
            payload = response.json()
        except ValueError:
            # Answering but not with JSON: something is at that address,
            # and it is not an OpenAI-compatible server.
            return (
                NodeHealth(
                    state=HealthState.DEGRADED,
                    detail='Answered, but not with a model list',
                    latency_ms=latency_ms,
                ),
                None,
            )

        return NodeHealth(state=HealthState.HEALTHY, latency_ms=latency_ms), payload

    async def _cached(self, node_id: str) -> NodeHealth | None:
        try:
            raw = await (await self._get_redis()).get(redis_key('health', node_id))
        except Exception as exc:  # noqa: BLE001 — a cache miss must never break the page
            logger.warning('Could not read cached health for %s: %s', node_id, exc)
            return None
        if raw is None:
            return None
        try:
            return NodeHealth.model_validate_json(raw)
        except ValueError:
            return None

    async def _remember(self, node_id: str, health: NodeHealth) -> None:
        try:
            await (await self._get_redis()).set(
                redis_key('health', node_id), health.model_dump_json(), ex=_CACHE_TTL_SECONDS
            )
        except Exception as exc:  # noqa: BLE001 — failing to cache is not failing
            logger.warning('Could not cache health for %s: %s', node_id, exc)


def combine(
    probe: NodeHealth,
    *,
    in_flight: int,
    max_concurrent: int,
    last_seen_seconds_ago: float | None,
    metrics: NodeMetrics | None = None,
) -> NodeHealth:
    """Fold what we know from elsewhere into the probe result.

    A node can answer a probe and still not be well: it may be saturated,
    or it may be a registered node that has stopped heartbeating while its
    endpoint stays up — which means whatever was supposed to be renewing
    the registration has died, and the node is minutes from dropping out
    of rotation with nothing having said so.

    Only ever downgrades. A probe that failed is not rescued by the node
    looking idle.
    """
    if probe.state is not HealthState.HEALTHY:
        return probe

    if last_seen_seconds_ago is not None and last_seen_seconds_ago > _HEARTBEAT_GRACE_SECONDS:
        return probe.model_copy(
            update={
                'state': HealthState.DEGRADED,
                'detail': f'Answering, but has not checked in for {int(last_seen_seconds_ago)}s',
            }
        )

    # A queue inside the node is the strongest signal available, and the
    # one our own gauge cannot produce. ``max_concurrent`` exists
    # precisely to stop work piling up somewhere we can neither see nor
    # balance; if it is piling up anyway, the limit is set too high for
    # this hardware — or somebody else is using the GPU.
    if metrics is not None and metrics.waiting:
        return probe.model_copy(
            update={
                'state': HealthState.DEGRADED,
                'detail': f'{metrics.waiting} request(s) queued on the node itself',
            }
        )

    if max_concurrent and in_flight >= max_concurrent:
        return probe.model_copy(update={'state': HealthState.DEGRADED, 'detail': 'At its concurrency limit'})

    return probe


def _short(exc: Exception) -> str:
    """One line an operator can read, not a stack of wrapped causes."""
    text = str(exc).strip().splitlines()
    return text[0][:120] if text else type(exc).__name__


def _model_ids(payload: object) -> list[str]:
    """The ids out of an OpenAI ``/models`` body.

    ``{"data": [{"id": "..."}]}`` is the shape every compatible server
    returns, and on a LoRA node the list holds the base model followed by
    one entry per adapter. Anything unrecognised yields nothing rather
    than raising: a node that answers oddly should cost its own model
    list, not the page.
    """
    if not isinstance(payload, dict):
        return []
    entries = payload.get('data')
    if not isinstance(entries, list):
        return []
    return [str(entry['id']) for entry in entries if isinstance(entry, dict) and entry.get('id')]
