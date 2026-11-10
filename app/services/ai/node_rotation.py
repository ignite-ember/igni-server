"""Taking a node out of rotation, and putting it back.

Works for any node, however it got here — registered, added from the
portal, or named in the static config. Edit and remove change a node's
*definition*, which only its owner can do; draining changes routing,
which is this deployment's own business, and an operator draining a GPU
during an incident should not first have to work out how it arrived.
"""

import logging

from app.api.base_v1.schema.portal.user import PortalUser
from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.fleet_schemas import NodeNotServing
from app.services.ai.node_drain import NodeDrain, NodeDrainRegistry
from app.services.ai.pool_manager import PoolManager

logger = logging.getLogger(__name__)


class NodeRotation:
    """Drain and resume, over the registry routing reads on every acquire."""

    def __init__(self, *, manager: PoolManager | None = None, drains: NodeDrainRegistry | None = None) -> None:
        self._manager = manager or PoolManager()
        self._drains = drains or self._manager.get_pool().drains

    async def drain(self, node_id: str, *, by: PortalUser, reason: str | None = None) -> NodeDrain:
        """Stop routing new requests to the node; in-flight ones finish on it.

        Raises `NodeNotServing` for an id nothing serves, because a drain
        on a typo is a decision that silently applies to nothing and looks
        like it worked. Nothing to invalidate: the drain is read on every
        acquire rather than baked into the cached backend list.
        """
        if not await self._is_served(node_id):
            raise NodeNotServing(node_id)
        record = await self._drains.drain(node_id, by=by.user_id, by_email=by.email, reason=reason)
        logger.info('Admin %s drained inference node %s', by.user_id, node_id)
        return record

    async def resume(self, node_id: str, *, by: PortalUser) -> None:
        """Let the node serve again.

        Not drained is success, and the node need not exist: clearing a
        drain left behind by a node that is gone is exactly the tidy-up
        somebody would want.
        """
        await self._drains.resume(node_id)
        logger.info('Admin %s resumed inference node %s', by.user_id, node_id)

    async def _is_served(self, node_id: str) -> bool:
        """Whether any of the three sources serves under this id.

        The provider already degrades a failed registry or database read to
        the sources it could read. If the config file itself cannot be
        read, allow the drain rather than refuse it: refusing to stop a
        node during whatever is going on is the worse error of the two.
        """
        try:
            backends = await self._manager.provider.load()
        except BackendConfigError:
            return False
        except OSError as exc:
            logger.warning('Could not read the backend list to check %s exists; draining anyway: %s', node_id, exc)
            return True
        return any(backend.id == node_id for backend in backends)
