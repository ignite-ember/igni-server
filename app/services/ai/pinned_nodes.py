"""Nodes an admin added from the portal: add, read, change, remove.

Only these have a definition this deployment owns. A registered node is
defined by whatever started the cluster and a static one by the config
file, so neither can be edited or removed here — but both still claim
their ids, which is why adding checks all three sources.
"""

import logging

from app.api.base_v1.schema.pinned_nodes import PinnedNodeCreate
from app.api.base_v1.schema.portal.user import PortalUser
from app.db.sql.inference_nodes import InferenceNodeDB
from app.db.sql.models.inference_node import InferenceNode
from app.db.sql.schemas.inference_node import NodeChanges, NodeIdAlreadyRegistered
from app.services.ai.backend import BackendInvalid
from app.services.ai.fleet_schemas import (
    NodeDefinitionInvalid,
    NodeIdAlreadyAdded,
    NodeIdInStaticConfig,
    NodeIdRegisteredByCluster,
    NodeNotAdded,
)
from app.services.ai.node_edit import NodeEdit
from app.services.ai.node_registry import NodeRegistry
from app.services.ai.pool_manager import PoolManager

logger = logging.getLogger(__name__)


class PinnedNodes:
    """The portal-added nodes, and the id policy that guards adding one."""

    def __init__(
        self,
        *,
        nodes: InferenceNodeDB | None = None,
        registry: NodeRegistry | None = None,
        manager: PoolManager | None = None,
    ) -> None:
        self._nodes = nodes or InferenceNodeDB()
        self._registry = registry or NodeRegistry()
        self._manager = manager or PoolManager()

    async def add(self, node: PinnedNodeCreate, *, by: PortalUser) -> InferenceNode:
        """Store a node; it is routed to immediately and never expires.

        Raises a `NodeIdTaken` subclass naming the source that already
        holds the id, and `NodeDefinitionInvalid` for a node the router
        could not use — checked by the backend's own rules, so the portal
        and the config file cannot disagree, and answered in its own words
        rather than a validation error that would echo the body back.
        """
        try:
            backend = node.backend()
        except BackendInvalid as exc:
            raise NodeDefinitionInvalid(node.node_id, reason=str(exc), adding=True) from exc
        await self._refuse_if_taken(node.node_id)
        try:
            # ``None``, not ``''``, for the org: the column is a nullable
            # foreign key to ``organizations.id`` (provenance, not
            # ownership), and no organisation has an empty id.
            created = await self._nodes.create(backend, org_id=by.org_id or None, created_by_user_id=by.user_id)
        except NodeIdAlreadyRegistered as exc:
            # Another request won the race between the check and the insert.
            raise NodeIdAlreadyAdded(node.node_id) from exc
        self._fleet_changed()
        logger.info('Admin %s added inference node %s', by.user_id, node.node_id)
        return created

    async def get(self, node_id: str) -> InferenceNode:
        """The stored definition. Raises `NodeNotAdded` for any other node."""
        node = await self._nodes.get_by_node_id(node_id)
        if node is None:
            raise NodeNotAdded(node_id)
        return node

    async def edit(self, node_id: str, changes: NodeChanges) -> InferenceNode:
        """Apply a partial edit. Raises `NodeNotAdded` for any other node.

        The edited node must still be a node the router can use, checked
        before anything is written: changing a Vertex node's region to
        nothing would otherwise store a definition every request then fails on.
        Raises `NodeDefinitionInvalid` when it is not.
        """
        stored = await self._nodes.get_by_node_id(node_id)
        if stored is None:
            raise NodeNotAdded(node_id)
        try:
            _edited, changes = NodeEdit(self._nodes.to_backend(stored), changes).result()
        except BackendInvalid as exc:
            raise NodeDefinitionInvalid(str(stored.node_id), reason=str(exc)) from exc
        node = await self._nodes.update(node_id, changes)
        if node is None:
            raise NodeNotAdded(node_id)
        self._fleet_changed()
        return node

    async def remove(self, node_id: str, *, by: PortalUser) -> None:
        """Delete the node. Already gone is success: it is out of rotation either way."""
        await self._nodes.delete(node_id)
        self._fleet_changed()
        logger.info('Admin %s removed inference node %s', by.user_id, node_id)

    def _fleet_changed(self) -> None:
        """This process just changed the fleet, so its backend cache is wrong.

        Other workers catch up on their own reload interval.
        """
        self._manager.provider.invalidate()

    async def _refuse_if_taken(self, node_id: str) -> None:
        if node_id in self._manager.provider.static_backend_ids():
            raise NodeIdInStaticConfig(node_id)
        if await self._nodes.get_by_node_id(node_id) is not None:
            raise NodeIdAlreadyAdded(node_id)
        if await self._registry.get(node_id) is not None:
            raise NodeIdRegisteredByCluster(node_id)
