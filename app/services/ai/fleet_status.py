"""How busy the customer's GPUs are, and what they serve.

Read-only. Every node is probed whatever its source, so a machine that
has been switched off reads as unreachable rather than looking exactly
like one serving traffic.
"""

import logging

from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.fleet_schemas import FleetSnapshot
from app.services.ai.health import NodeHealthProbe
from app.services.ai.node_registry import NodeRegistry
from app.services.ai.pool_manager import PoolManager

logger = logging.getLogger(__name__)


class FleetStatus:
    """The fleet's live state, for the portal's inference page."""

    def __init__(
        self,
        *,
        manager: PoolManager | None = None,
        registry: NodeRegistry | None = None,
        probe: NodeHealthProbe | None = None,
    ) -> None:
        self._manager = manager or PoolManager()
        self._registry = registry or NodeRegistry()
        self._probe = probe or NodeHealthProbe()

    async def snapshot(self) -> FleetSnapshot:
        """Every node with its load, health and the node's own gauges.

        A deployment that has configured no GPUs yet, or whose config file
        is currently broken, gets an empty fleet — the page shows the
        reason, rather than a 500 that tells them nothing.
        """
        provider = self._manager.provider
        registered = {node.backend.id: node for node in await self._registry.list_nodes()}
        try:
            backends = await provider.load()
            sources = await provider.source_of()
        except BackendConfigError:
            backends, sources = [], {}
        health = await self._probe.check_all(backends)
        # Asked for separately because a healthy node that is not vLLM
        # simply has none, which is not a failure of anything.
        metrics = await self._probe.metrics_for(backends)

        try:
            statuses = await self._manager.get_pool().backend_statuses(
                registered=registered, sources=sources, health=health, metrics=metrics
            )
        except BackendConfigError:
            statuses = []
        return FleetSnapshot.of(statuses)

    async def model_ids(self) -> list[str]:
        """Every model id some node serves, sorted; empty when no GPUs are configured yet."""
        try:
            pairs = await self._manager.get_pool().list_available_models()
        except BackendConfigError:
            return []
        return sorted({name for name, _ in pairs})
