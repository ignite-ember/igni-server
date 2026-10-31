"""The API a GPU cluster calls when it starts and stops.

Three verbs, and they map onto what the customer's automation already
does:

* ``POST /nodes`` when the node is up and vLLM is answering.
* ``POST /nodes/{id}/heartbeat`` on a timer while it keeps serving.
* ``DELETE /nodes/{id}`` when the cluster is being torn down.

The heartbeat is what makes the ugly case work. A stop-cluster job runs
the delete; a spot reclaim, a kernel panic and a cut network do not, and
those are the ones that actually hurt, because a node still in rotation
that no longer answers fails every request routed to it. Registrations
expire, so the third case looks like the first, one heartbeat interval
later.

Authenticated by ``X-Igni-Node-Token`` on the whole subtree rather than
per route, so a fourth verb cannot be added unauthenticated by omission.

A change drops this process's cached backend list, so the next request
sees it rather than waiting out the reload interval.
"""

import logging

from fastapi import APIRouter, Body, HTTPException, Request, Response, status

from app.api.base_v1.schema.nodes import NodeRegistration, NodeRegistrationAccepted
from app.middlewares.audit import set_audit_detail
from app.services.ai.node_registry import HEARTBEAT_INTERVAL_SECONDS, NodeRegistry
from app.services.ai.pool_manager import PoolManager

logger = logging.getLogger(__name__)

router = APIRouter()


def _audit(request: Request, **detail: object) -> None:
    """Attach the facts to the audit record F90 writes for this request.

    The principal is named rather than left blank. These routes
    authenticate with a shared node token, so there is no user behind
    them — `actor_user_id` is null and stays null. An operator reading
    the trail needs to know the *kind* of credential used, because "the
    node token did this from that address" is the whole of what can be
    said, and a row with no actor and no explanation reads like a bug in
    the audit trail rather than a fact about the caller.
    """
    set_audit_detail(request, {'principal': 'node-token', **detail})


@router.post(
    '/nodes',
    response_model=NodeRegistrationAccepted,
    status_code=status.HTTP_201_CREATED,
    description='Announce an inference node as available. Idempotent — re-registering an id replaces it.',
)
async def register_node(request: Request, body: NodeRegistration = Body(...)) -> NodeRegistrationAccepted:
    backend = body.to_backend()

    # A statically-configured id is the operator's deliberate choice, and
    # silently shadowing it would surface later as a routing mystery. Far
    # better to fail here, where the automation that got the id wrong is
    # watching.
    if backend.id in PoolManager().provider.static_backend_ids():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'Backend id {backend.id!r} is defined in this deployment’s static configuration '
                'and cannot be registered dynamically. Choose a different id, or remove it from '
                'inference_backends.'
            ),
        )

    # Whether this replaces a live registration, which is the difference
    # between a cluster starting and somebody redirecting one.
    existing = await NodeRegistry().get(backend.id)

    node = await NodeRegistry().register(backend)

    # What makes this record worth keeping: the address prompts will now
    # be sent to. Registration is idempotent, so re-registering an id
    # *replaces* where its traffic goes — and without this the trail
    # would say a node was registered without saying where. Never the
    # api_key, which is a credential for the node's own proxy.
    _audit(
        request,
        node_id=backend.id,
        base_url=backend.base_url,
        models=list(backend.models),
        pools=list(backend.pools),
        replaced=bool(existing),
    )

    _forget_cached_backends()
    return NodeRegistrationAccepted(
        id=backend.id,
        registered_at=node.registered_at,
        heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
    )


@router.post(
    '/nodes/{node_id}/heartbeat',
    response_model=NodeRegistrationAccepted,
    description='Keep a registration alive. 404 if it has expired, meaning the node should register again.',
)
async def heartbeat_node(node_id: str) -> NodeRegistrationAccepted:
    node = await NodeRegistry().heartbeat(node_id)
    if node is None:
        # 404 rather than resurrecting it: a heartbeat carries only an id,
        # and we need the node's address and capacity back before we can
        # route to it again. The caller's correct response is to
        # re-register, and this is how it is told to.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Node {node_id!r} is not registered — register it again',
        )
    return NodeRegistrationAccepted(
        id=node_id,
        registered_at=node.registered_at,
        heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
    )


@router.delete(
    '/nodes/{node_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    description='Remove a node from rotation. Idempotent.',
)
async def deregister_node(request: Request, node_id: str) -> Response:
    # Deleting something already gone is success, not an error: a
    # stop-cluster job that retries after a timeout must not fail the
    # second time, and the caller's goal — the node is not in rotation —
    # holds either way.
    await NodeRegistry().deregister(node_id)
    _audit(request, node_id=node_id)
    _forget_cached_backends()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _forget_cached_backends() -> None:
    """We just edited the registry: drop this process's cached backend list.

    The reload interval exists for workers that have no idea anything
    happened; this process does, and must not keep routing to a node it
    has only this moment removed.
    """
    PoolManager().provider.invalidate()
