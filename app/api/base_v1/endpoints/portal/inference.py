"""What the portal shows about the customer's GPUs, and how it manages them.

A self-hosted deployment's inference capacity is the customer's own
hardware and their own bill, which makes "how busy is it, and do we need
another one" a question they ask us rather than a provider. This is the
page that answers it, and — for nodes added here — the place they are
managed from.

## Everything is shown, and everything has a colour

Nodes arrive from three places and used to carry wildly different amounts
of liveness information: registered ones had a heartbeat, static ones had
nothing at all. So a machine that had been switched off looked exactly
like one serving traffic.

Now every node is probed, whatever its source, and reports healthy,
degraded or unreachable. Nothing is hidden for failing — a node an admin
added that cannot be reached stays on the page and turns red, because
"it disappeared" is a worse answer than "it is down".

## Admin-only

Base URLs and node names describe the shape of a private network, and
the write endpoints add machines that every developer's prompts get sent
to. An ordinary member has no reason to enumerate the first and must not
be able to do the second.
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status

from app.api.base_v1.schema.nodes import (
    AvailableModels,
    InferenceStatusView,
    NodeDrainRequest,
    NodeDrainView,
    NodeProbeRequest,
    NodeProbeResult,
    NodeStatusView,
    PinnedNodeCreate,
    PinnedNodeUpdate,
    PinnedNodeView,
    PoolStatusView,
)
from app.api.base_v1.schema.portal.user import PortalUser
from app.config import settings
from app.db.sql.inference_nodes import InferenceNodeDB
from app.db.sql.schemas.inference_node import NodeIdAlreadyRegistered
from app.security.portal_auth import get_current_portal_user
from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.health import NodeHealthProbe
from app.services.ai.node_drain import NodeDrainRegistry
from app.services.ai.node_registry import HEARTBEAT_INTERVAL_SECONDS, NodeRegistry
from app.services.ai.pool_manager import PoolManager
from app.services.ai.schemas import HealthState, VALID_POOL_TYPES

logger = logging.getLogger(__name__)

router = APIRouter()

_nodes = InferenceNodeDB()
_drains = NodeDrainRegistry()


def require_admin(current_user: PortalUser = Depends(get_current_portal_user)) -> PortalUser:
    """An org admin: may see the pool's state and the models it serves."""
    if current_user.org_role != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Only org admins can see inference nodes',
        )
    return current_user


def require_node_manager(current_user: PortalUser = Depends(get_current_portal_user)) -> PortalUser:
    """A super admin: may add, change, probe, drain or remove a node.

    The pool serves every organisation on the install, so a node one
    organisation's admin adds would receive the others' prompts, and a node
    they drain would push the others' traffic elsewhere. Changing it is the
    install's decision, not one organisation's (product gap SER-32).
    """
    if not current_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Only a super admin can change inference nodes: they serve every organisation',
        )
    return current_user


@router.get(
    '/inference',
    response_model=InferenceStatusView,
    status_code=status.HTTP_200_OK,
    description='Live inference node statistics and health for both pools.',
)
async def get_inference_status(current_user: PortalUser = Depends(require_admin)) -> InferenceStatusView:
    manager = PoolManager()
    provider = manager.provider

    # Read once for both pools. A node serving both would otherwise be
    # looked up, labelled and probed twice to produce the same answer.
    registered = {node.backend.id: node for node in await NodeRegistry().list_nodes()}
    try:
        backends = await provider.load()
        sources = await provider.source_of()
    except BackendConfigError:
        backends, sources = [], {}
    probe = NodeHealthProbe()
    health = await probe.check_all(backends)
    # The node's own gauges, where it publishes them. Asked for
    # separately because a healthy node that is not vLLM simply has none,
    # which is not a failure of anything.
    metrics = await probe.metrics_for(backends)

    pools = []
    for pool_type in VALID_POOL_TYPES:
        try:
            statuses = await manager.get_pool(pool_type).backend_statuses(
                registered=registered, sources=sources, health=health, metrics=metrics
            )
        except BackendConfigError:
            # A deployment that has configured no GPUs yet, or whose
            # config file is currently broken, should see an empty
            # cluster and the reason on the page — not a 500 that tells
            # them nothing.
            statuses = []
        # Totals over what can actually take work. A drained node's
        # capacity is not capacity — counting it overstates the number an
        # operator reads to decide whether they need another GPU. Its own
        # row still shows its in-flight count, which is what you watch
        # fall to zero before doing maintenance on it.
        serving = [s for s in statuses if s.drain is None]
        in_flight = sum(s.in_flight for s in serving)
        capacity = sum(s.max_concurrent for s in serving)
        pools.append(
            PoolStatusView(
                pool=pool_type,
                nodes=[NodeStatusView.of(s) for s in statuses],
                total_in_flight=in_flight,
                total_capacity=capacity,
                utilization=in_flight / capacity if capacity else 0.0,
            )
        )

    return InferenceStatusView(
        pools=pools,
        registration_enabled=bool(settings.node_registration_token),
        heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
    )


@router.get(
    '/inference/models',
    response_model=AvailableModels,
    status_code=status.HTTP_200_OK,
    description='Model ids the chat pool serves, for choosing a group default.',
)
async def list_models(current_user: PortalUser = Depends(require_admin)) -> AvailableModels:
    """What the portal can offer as a group's model.

    A portal-authenticated twin of ``/chat/models``. The portal must not
    call the client endpoint: that one authenticates a real user JWT, the
    portal session is a different scheme, and the 401 it returns is
    indistinguishable to the browser from an expired session — so asking
    for it logs the admin out.
    """
    try:
        pairs = await PoolManager().get_chat_pool().list_available_models('chat')
    except BackendConfigError:
        # No GPUs configured yet is a state to render, not an error.
        return AvailableModels(models=[])
    return AvailableModels(models=sorted({name for name, _ in pairs}))


@router.post(
    '/inference/nodes/discover',
    response_model=NodeProbeResult,
    status_code=status.HTTP_200_OK,
    description='Ask a node which models it serves, before adding it.',
)
async def discover_node(
    body: NodeProbeRequest = Body(...),
    current_user: PortalUser = Depends(require_node_manager),
) -> NodeProbeResult:
    """Read the node's own model list.

    So an admin pastes an address and picks from what is actually there,
    rather than typing ids that have to match exactly. A mistyped id does
    not error — the node simply never gets selected for that model — and
    LoRA adapter names are whatever the operator passed to
    ``--lora-modules``, which nobody remembers.
    """
    served = await NodeHealthProbe().list_models(base_url=body.base_url, api_key=body.api_key)
    return NodeProbeResult(
        reachable=served.health.state is not HealthState.UNREACHABLE,
        models=served.models,
        detail=served.health.detail,
        latency_ms=served.health.latency_ms,
        needs_credentials=served.needs_credentials,
    )


@router.post(
    '/inference/nodes',
    response_model=PinnedNodeView,
    status_code=status.HTTP_201_CREATED,
    description='Add a GPU node by hand. It is routed to immediately and never expires.',
)
async def add_node(
    body: PinnedNodeCreate = Body(...),
    current_user: PortalUser = Depends(require_node_manager),
) -> PinnedNodeView:
    await _refuse_if_taken(body.node_id)

    try:
        node = await _nodes.create(
            node_id=body.node_id,
            # ``None``, not ``''``. The column carries a foreign key to
            # ``organizations.id`` and no organisation has an empty id, so
            # the old fallback would have failed the constraint on any
            # admin whose token carried no org — it was reachable only
            # because that does not happen for an admin today. Now that
            # the column is nullable (provenance, not ownership) there is
            # a correct value for it.
            org_id=current_user.org_id or None,
            base_url=body.base_url,
            models=list(body.models),
            api_key=body.api_key or None,
            max_concurrent=body.max_concurrent,
            pools=list(body.pools),
            multimodal=body.multimodal,
            created_by_user_id=current_user.user_id,
        )
    except NodeIdAlreadyRegistered as exc:
        # The status lives here, not in the DB class. Two nodes racing
        # on one id is a conflict about this request; the persistence
        # layer's job was to notice it, not to pick a status code.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    # This process just changed the fleet, so its 30-second cache is
    # wrong; other workers catch up on their own interval.
    PoolManager().provider.invalidate()
    logger.info('Admin %s added inference node %s', current_user.user_id, body.node_id)
    return PinnedNodeView.of(node)


@router.get(
    '/inference/nodes/{node_id}',
    response_model=PinnedNodeView,
    description='One node added here, for the edit form. 404 for a node this deployment did not add.',
)
async def get_node(node_id: str, current_user: PortalUser = Depends(require_admin)) -> PinnedNodeView:
    """The stored definition, not the live status.

    `GET /inference` answers what the fleet is *doing* — load, health,
    heartbeats — and is the wrong source for a form: it reports a
    node's live pools rather than its stored ones, and carries no
    `enabled` at all. An edit form needs the row as written, which is
    what this returns.

    404 for a registered or statically-configured node, deliberately.
    Those are defined elsewhere — by whatever started the cluster, or by
    the config file — and offering a form that cannot save is worse than
    offering none.
    """
    node = await _nodes.get_by_node_id(node_id)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'No node {node_id!r} was added here. A registered node is defined by whatever started it.',
        )
    return PinnedNodeView.of(node)


@router.patch(
    '/inference/nodes/{node_id}',
    response_model=PinnedNodeView,
    description='Change a node added here. Omitting api_key leaves the stored one alone.',
)
async def edit_node(
    node_id: str,
    body: PinnedNodeUpdate = Body(...),
    current_user: PortalUser = Depends(require_node_manager),
) -> PinnedNodeView:
    node = await _nodes.update(node_id, body.changes())
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'No node {node_id!r} was added here')
    PoolManager().provider.invalidate()
    return PinnedNodeView.of(node)


@router.delete(
    '/inference/nodes/{node_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    description='Remove a node added here. Idempotent.',
)
async def remove_node(node_id: str, current_user: PortalUser = Depends(require_node_manager)) -> Response:
    # Deleting something already gone is success: the caller's goal — the
    # node is not in rotation — holds either way.
    await _nodes.delete(node_id)
    PoolManager().provider.invalidate()
    logger.info('Admin %s removed inference node %s', current_user.user_id, node_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    '/inference/nodes/{node_id}/drain',
    response_model=NodeDrainView,
    description=(
        'Stop routing new requests to this node. Works for any node, however it got here — '
        'registered, added from the portal, or named in the static config. In-flight requests '
        'finish on it. The decision has no expiry and survives the node re-registering or '
        'restarting; only Resume clears it.'
    ),
)
async def drain_node(
    node_id: str,
    body: NodeDrainRequest = Body(default=NodeDrainRequest()),
    current_user: PortalUser = Depends(require_node_manager),
) -> NodeDrainView:
    """Take a node out of rotation.

    Deliberately not restricted to nodes added here, unlike edit and
    remove. Those change a *definition*, which only its owner can do —
    a registered node is defined by whatever started the cluster. This
    changes routing, which is this deployment's own business, and an
    operator draining a GPU during an incident should not first have to
    work out how it arrived.

    Refuses an id nothing serves, because a drain on a typo is a decision
    that silently applies to nothing and looks like it worked.
    """
    if not await _node_exists(node_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'No inference node {node_id!r} is serving this deployment',
        )

    record = await _drains.drain(
        node_id,
        by=current_user.user_id,
        by_email=current_user.email,
        reason=body.reason,
    )
    # Nothing to invalidate — the drain is read on every acquire rather
    # than baked into the cached backend list, so it applies to the next
    # request without waiting for a cache to turn over.
    logger.info('Admin %s drained inference node %s', current_user.user_id, node_id)
    return NodeDrainView(**record.model_dump(exclude={'node_id'}))


@router.delete(
    '/inference/nodes/{node_id}/drain',
    status_code=status.HTTP_204_NO_CONTENT,
    description='Let this node serve again. Idempotent.',
)
async def resume_node(node_id: str, current_user: PortalUser = Depends(require_node_manager)) -> Response:
    # Resuming something that was not drained is success: the caller's
    # goal — this node can serve — holds either way. And unlike `drain`,
    # this does not check the node exists: clearing a drain left behind by
    # a node that is gone is exactly the tidy-up somebody would want.
    await _drains.resume(node_id)
    logger.info('Admin %s resumed inference node %s', current_user.user_id, node_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _node_exists(node_id: str) -> bool:
    """Whether anything currently serves under this id.

    Any of the three sources counts. Best-effort on the backend list: if
    it cannot be read, allow the drain rather than refuse it, because
    refusing to stop a node during whatever is going on is the worse
    error of the two.
    """
    try:
        backends = await PoolManager().provider.load()
    except BackendConfigError:
        return False
    except Exception:  # noqa: BLE001 — do not block a drain on a read failure
        return True
    return any(backend.id == node_id for backend in backends)


async def _refuse_if_taken(node_id: str) -> None:
    """Reject an id already in use, naming which source holds it.

    Ids namespace the Redis concurrency gauge, so two backends sharing
    one would each appear half as loaded as they are — and the operator
    would see a node they cannot edit sitting under a name they think
    they own.
    """
    if node_id in PoolManager().provider.static_backend_ids():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'Backend id {node_id!r} is in this deployment’s static configuration. '
                'Choose a different id, or remove it from inference_backends.'
            ),
        )
    if await _nodes.get_by_node_id(node_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'A node called {node_id!r} has already been added here.',
        )
    if await NodeRegistry().get(node_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'A node called {node_id!r} is currently registered by your cluster automation. '
                'Adding it here as well would give one machine two entries.'
            ),
        )
