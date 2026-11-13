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

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status

from app.api.base_v1.schema.nodes import (
    AvailableModels,
    InferenceStatusView,
    NodeDrainRequest,
    NodeDrainView,
    NodeProbeRequest,
    NodeProbeResult,
    PinnedNodeCreate,
    PinnedNodeUpdate,
    PinnedNodeView,
)
from app.api.base_v1.schema.portal.user import PortalUser
from app.config import settings
from app.security.portal_auth import get_current_portal_user, require_admin
from app.services.ai.fleet_schemas import NodeDefinitionInvalid, NodeIdTaken, NodeNotAdded, NodeNotServing
from app.services.ai.fleet_status import FleetStatus
from app.services.ai.health import NodeHealthProbe
from app.services.ai.node_registry import HEARTBEAT_INTERVAL_SECONDS
from app.services.ai.node_rotation import NodeRotation
from app.services.ai.pinned_nodes import PinnedNodes
from app.services.ai.schemas import HealthState

router = APIRouter()


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


# Providers rather than `Depends(FleetStatus)`: FastAPI would read the
# constructors' keyword arguments as query parameters.


def fleet_status() -> FleetStatus:
    return FleetStatus()


def pinned_nodes() -> PinnedNodes:
    return PinnedNodes()


def node_rotation() -> NodeRotation:
    return NodeRotation()


def node_probe() -> NodeHealthProbe:
    return NodeHealthProbe()


@router.get(
    '/inference',
    response_model=InferenceStatusView,
    status_code=status.HTTP_200_OK,
    description='Live inference node statistics and health.',
)
async def get_inference_status(
    current_user: PortalUser = Depends(require_admin),
    fleet: FleetStatus = Depends(fleet_status),
) -> InferenceStatusView:
    return InferenceStatusView.of(
        await fleet.snapshot(),
        registration_enabled=bool(settings.node_registration_token),
        heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
    )


@router.get(
    '/inference/models',
    response_model=AvailableModels,
    status_code=status.HTTP_200_OK,
    description='Model ids the inference nodes serve, for choosing a group default.',
)
async def list_models(
    current_user: PortalUser = Depends(require_admin),
    fleet: FleetStatus = Depends(fleet_status),
) -> AvailableModels:
    """What the portal can offer as a group's model.

    A portal-authenticated twin of ``/chat/models``. The portal must not
    call the client endpoint: that one authenticates a real user JWT, the
    portal session is a different scheme, and the 401 it returns is
    indistinguishable to the browser from an expired session — so asking
    for it logs the admin out.
    """
    return AvailableModels(models=await fleet.model_ids())


@router.post(
    '/inference/nodes/discover',
    response_model=NodeProbeResult,
    status_code=status.HTTP_200_OK,
    description='Ask a node which models it serves, before adding it.',
)
async def discover_node(
    body: NodeProbeRequest = Body(...),
    current_user: PortalUser = Depends(require_node_manager),
    probe: NodeHealthProbe = Depends(node_probe),
) -> NodeProbeResult:
    """Read the node's own model list.

    So an admin pastes an address and picks from what is actually there,
    rather than typing ids that have to match exactly. A mistyped id does
    not error — the node simply never gets selected for that model — and
    LoRA adapter names are whatever the operator passed to
    ``--lora-modules``, which nobody remembers.
    """
    served = await probe.list_models(base_url=body.base_url, api_key=body.api_key)
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
    nodes: PinnedNodes = Depends(pinned_nodes),
) -> PinnedNodeView:
    try:
        node = await nodes.add(body, by=current_user)
    except NodeIdTaken as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PinnedNodeView.of(node)


@router.get(
    '/inference/nodes/{node_id}',
    response_model=PinnedNodeView,
    description='One node added here, for the edit form. 404 for a node this deployment did not add.',
)
async def get_node(
    node_id: str,
    current_user: PortalUser = Depends(require_admin),
    nodes: PinnedNodes = Depends(pinned_nodes),
) -> PinnedNodeView:
    """The stored definition, not the live status.

    `GET /inference` answers what the fleet is *doing* — load, health,
    heartbeats — and is the wrong source for a form: it reports a
    node's live state rather than its stored definition, and carries no
    `enabled` at all. An edit form needs the row as written, which is
    what this returns.

    404 for a registered or statically-configured node, deliberately.
    Those are defined elsewhere — by whatever started the cluster, or by
    the config file — and offering a form that cannot save is worse than
    offering none.
    """
    try:
        node = await nodes.get(node_id)
    except NodeNotAdded as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'{exc}. A registered node is defined by whatever started it.',
        ) from exc
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
    nodes: PinnedNodes = Depends(pinned_nodes),
) -> PinnedNodeView:
    try:
        node = await nodes.edit(node_id, body.changes())
    except NodeNotAdded as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NodeDefinitionInvalid as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return PinnedNodeView.of(node)


@router.delete(
    '/inference/nodes/{node_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    description='Remove a node added here. Idempotent.',
)
async def remove_node(
    node_id: str,
    current_user: PortalUser = Depends(require_node_manager),
    nodes: PinnedNodes = Depends(pinned_nodes),
) -> Response:
    await nodes.remove(node_id, by=current_user)
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
    rotation: NodeRotation = Depends(node_rotation),
) -> NodeDrainView:
    try:
        record = await rotation.drain(node_id, by=current_user, reason=body.reason)
    except NodeNotServing as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NodeDrainView(**record.model_dump(exclude={'node_id'}))


@router.delete(
    '/inference/nodes/{node_id}/drain',
    status_code=status.HTTP_204_NO_CONTENT,
    description='Let this node serve again. Idempotent.',
)
async def resume_node(
    node_id: str,
    current_user: PortalUser = Depends(require_node_manager),
    rotation: NodeRotation = Depends(node_rotation),
) -> Response:
    await rotation.resume(node_id, by=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
