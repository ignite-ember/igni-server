"""Where the inference backend list comes from.

Configuration the customer owns, not a secret we fetch. This replaces
`AIKeySecretProvider`, which read a JSON payload out of GCP Secret
Manager — an air-gapped deployment has no GCP project, and even a
cloud-hosted one should not need our secret store to describe its own
GPUs.

Three sources, and the split is about who edits them:

* ``inference_backends_path`` — a JSON file. A Kubernetes ConfigMap or a
  bind mount, edited by whoever manages the cluster. The right model for
  hardware that is always on.
* ``inference_backends`` — the same JSON inline in an environment
  variable, for docker-compose and single-node installs where a file
  mount is more ceremony than it is worth.
* The **node registry** — nodes that announced themselves at runtime.
  See ``node_registry``. This is for clusters that start and stop, where
  editing a file and waiting for a rollout is not a workflow anyone will
  run twice.
* **Pinned nodes** — added by an admin in the portal and stored in
  Postgres. See ``InferenceNode``. For the machine somebody has just been
  handed the address of, where neither a redeploy nor cluster automation
  is available.

Precedence runs static, then pinned, then registered: the further from
the deployment's own configuration, the less it gets to override.

A deployment can use any combination, including none of the first two,
and the list is re-read on a short TTL so adding a node does not require
a restart — which matters, because adding a node is the customer's
response to being slow.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import time

from pydantic import ValidationError

from app.config import settings
from app.db.sql.inference_nodes import InferenceNodeDB
from app.services.ai.backend import InferenceBackend
from app.services.ai.node_registry import NodeRegistry
from app.services.secrets import SecretCipherUnavailable, SecretDecryptError

logger = logging.getLogger(__name__)

# How long a loaded backend list is trusted before re-reading. Short
# enough that adding a GPU takes effect within a coffee break, long
# enough that we are not stat-ing a file and scanning Redis on every
# completion.
_RELOAD_INTERVAL_SECONDS = 30


class BackendConfigError(RuntimeError):
    """The configured backend list is missing or malformed.

    Raised at load rather than swallowed: a typo in the node list should
    fail loudly at startup, not silently route every request to the one
    backend that happened to parse.
    """


class InferenceBackendProvider:
    """Loads and caches the merged static + registered backend list."""

    def __init__(
        self,
        *,
        path: str | None = None,
        inline: str | None = None,
        registry: NodeRegistry | None = None,
        pinned: InferenceNodeDB | None = None,
    ) -> None:
        self._path = path if path is not None else settings.inference_backends_path
        self._inline = inline if inline is not None else settings.inference_backends
        self._registry = registry or NodeRegistry()
        self._pinned = pinned or InferenceNodeDB()
        self._cached: list[InferenceBackend] | None = None
        self._loaded_at: float = 0.0

    async def load(self) -> list[InferenceBackend]:
        """The current backend list, re-read when the cache is stale."""
        now = time.monotonic()
        if self._cached is not None and (now - self._loaded_at) < _RELOAD_INTERVAL_SECONDS:
            return self._cached

        backends = await self._read()
        self._cached = backends
        self._loaded_at = now
        return backends

    def invalidate(self) -> None:
        """Drop the cached list so the next load re-reads.

        For the process that *just changed the registry*. The reload
        interval is the right answer for a worker that has no idea
        anything happened, and the wrong one for the one that made it
        happen: without this, a node deregistered a moment ago is still
        routed to for the rest of the interval, and — worse — the list
        pushed to code-index on the way out still contains it.

        Other processes keep their own caches and catch up on their own
        interval, which is the behaviour the interval exists to define.
        """
        self._cached = None

    async def source_of(self) -> dict[str, str]:
        """Where each backend came from, for the portal's node table.

        Rebuilt from the same reads rather than tagged onto
        ``InferenceBackend``: routing does not care where a node came
        from, and putting the answer on the model would push a display
        concern into the thing that has to stay small.
        """
        static_ids = {backend.id for backend in self._read_static()}
        sources = dict.fromkeys(static_ids, 'static')
        try:
            for row in await self._pinned.list_all():
                if str(row.node_id) not in sources:
                    sources[str(row.node_id)] = 'pinned'
        except Exception as exc:  # noqa: BLE001 — the table is decoration, not routing
            logger.warning('Could not read pinned nodes while labelling sources: %s', exc)
        return sources

    def static_backend_ids(self) -> set[str]:
        """Ids pinned in configuration.

        The registration endpoint checks this so a node claiming a
        statically-configured id is refused where the automation that
        made the mistake can see it, rather than silently shadowing the
        operator's own configuration.
        """
        return {backend.id for backend in self._read_static()}

    @staticmethod
    def routing_map(backends: list[InferenceBackend]) -> dict[str, str]:
        """id → the fields whose change invalidates a sticky assignment.

        Capacity is not among them: raising a node's concurrency limit
        should not throw away its warm caches. The pool stores this in
        Redis and diffs against it — see
        ``InferenceBackendPool._flush_sticky_if_config_changed``.
        """
        return {backend.id: f'{backend.base_url}|{",".join(sorted(backend.models))}' for backend in backends}

    # ── Internals ────────────────────────────────────────────────────

    async def _read(self) -> list[InferenceBackend]:
        static = self._read_static()
        taken = {backend.id for backend in static}

        pinned = await self._read_pinned(taken)
        taken |= {backend.id for backend in pinned}

        registered = await self._read_registered(static_ids=taken)

        if not static and not pinned and not registered:
            raise BackendConfigError(
                'No inference backends are available. Set inference_backends_path to a JSON '
                'file, or inference_backends to the same JSON inline, add one from the portal, '
                'or have your nodes register themselves against /v1/nodes.'
            )
        return static + pinned + registered

    async def _read_pinned(self, taken: set[str]) -> list[InferenceBackend]:
        """Nodes an admin added, minus anything the static config names.

        Best-effort like the registry: a database blip should cost the
        pinned nodes rather than take routing down for the statically
        configured ones.
        """
        try:
            rows = await self._pinned.list_all()
        except Exception as exc:  # noqa: BLE001 — a stored node must not be able to break routing
            logger.warning('Could not read pinned inference nodes, skipping them: %s', exc)
            return []

        nodes = InferenceNodeDB()
        backends: list[InferenceBackend] = []
        for row in rows:
            if not row.enabled or str(row.node_id) in taken:
                continue
            try:
                backends.append(nodes.to_backend(row))
            except (SecretDecryptError, SecretCipherUnavailable) as exc:
                # Its key will not decrypt (rotated without keeping the
                # old one, or no key configured): this node goes, the
                # others route.
                logger.error('Skipping pinned node %s: its stored API key cannot be read: %s', row.node_id, exc)
        return backends

    def _read_static(self) -> list[InferenceBackend]:
        raw = self._read_raw()
        if raw is None:
            return []

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BackendConfigError(f'Inference backend config is not valid JSON: {exc}') from exc

        if not isinstance(payload, list):
            raise BackendConfigError(
                f'Inference backend config must be a JSON array of backends, got {type(payload).__name__}'
            )

        try:
            backends = [InferenceBackend.model_validate(entry) for entry in payload]
        except ValidationError as exc:
            raise BackendConfigError(f'Inference backend config is invalid: {exc}') from exc

        duplicate = _first_duplicate([b.id for b in backends])
        if duplicate is not None:
            # Ids namespace Redis keys, so duplicates would silently share
            # a concurrency gauge and each appear half as loaded as it is.
            raise BackendConfigError(f'Duplicate inference backend id {duplicate!r}')

        return backends

    async def _read_registered(self, *, static_ids: set[str]) -> list[InferenceBackend]:
        """Live registrations, minus anything the static config also names.

        The registry is best-effort in a way the file is not: Redis being
        briefly unreachable should degrade to the statically-configured
        nodes rather than take inference down entirely. A file that fails
        to parse still raises, because that is a mistake somebody just
        made and needs to see.
        """
        try:
            nodes = await self._registry.list_nodes()
        except Exception as exc:  # noqa: BLE001 — the registry must not be able to break routing
            logger.warning('Could not read the node registry, using static backends only: %s', exc)
            return []

        return [node.backend for node in nodes if node.backend.id not in static_ids]

    def _read_raw(self) -> str | None:
        """The static JSON, or None when this deployment configures none."""
        if self._path:
            path = Path(self._path)
            if not path.exists():
                raise BackendConfigError(f'inference_backends_path points at {self._path}, which does not exist')
            return path.read_text()
        return self._inline or None


def _first_duplicate(values: list[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None
