"""What the fleet services answer with, and the refusals they raise.

Separate from the services so a router can catch a refusal by name and a
schema can project a snapshot without importing the classes that build
them.
"""

from pydantic import BaseModel

from app.services.ai.schemas import BackendStatus


class FleetSnapshot(BaseModel):
    """Every node with its load, and the totals over what can take work."""

    nodes: list[BackendStatus]
    total_in_flight: int
    total_capacity: int

    @classmethod
    def of(cls, statuses: list[BackendStatus]) -> 'FleetSnapshot':
        """Total over the nodes that are serving.

        A drained node's capacity is not capacity — counting it overstates
        the number an operator reads to decide whether they need another
        GPU. Its own row still shows its in-flight count, which is what you
        watch fall to zero before doing maintenance on it.
        """
        serving = [s for s in statuses if s.drain is None]
        return cls(
            nodes=statuses,
            total_in_flight=sum(s.in_flight for s in serving),
            total_capacity=sum(s.max_concurrent for s in serving),
        )

    @property
    def utilization(self) -> float:
        return self.total_in_flight / self.total_capacity if self.total_capacity else 0.0


class NodeIdTaken(Exception):
    """A node id is already in use, and the subclass says where.

    Ids namespace the Redis concurrency gauge, so two backends sharing one
    would each appear half as loaded as they are — and the operator would
    see a node they cannot edit sitting under a name they think they own.
    """

    def __init__(self, node_id: str, message: str) -> None:
        super().__init__(message)
        self.node_id = node_id


class NodeIdInStaticConfig(NodeIdTaken):
    def __init__(self, node_id: str) -> None:
        super().__init__(
            node_id,
            f'Backend id {node_id!r} is in this deployment’s static configuration. '
            'Choose a different id, or remove it from inference_backends.',
        )


class NodeIdAlreadyAdded(NodeIdTaken):
    def __init__(self, node_id: str) -> None:
        super().__init__(node_id, f'A node called {node_id!r} has already been added here.')


class NodeIdRegisteredByCluster(NodeIdTaken):
    def __init__(self, node_id: str) -> None:
        super().__init__(
            node_id,
            f'A node called {node_id!r} is currently registered by your cluster automation. '
            'Adding it here as well would give one machine two entries.',
        )


class NodeNotAdded(LookupError):
    """No node under this id was added from the portal.

    A registered or statically-configured node may well exist under it;
    those are defined elsewhere and cannot be edited here.
    """

    def __init__(self, node_id: str) -> None:
        super().__init__(f'No node {node_id!r} was added here')
        self.node_id = node_id


class NodeDefinitionInvalid(ValueError):
    """An edit would leave a node the router cannot use; nothing was written."""

    def __init__(self, node_id: str, reason: str) -> None:
        super().__init__(f"Node '{node_id}' was not changed: {reason}")
        self.node_id = node_id


class NodeNotServing(LookupError):
    """Nothing serves under this id, from any source."""

    def __init__(self, node_id: str) -> None:
        super().__init__(f'No inference node {node_id!r} is serving this deployment')
        self.node_id = node_id
