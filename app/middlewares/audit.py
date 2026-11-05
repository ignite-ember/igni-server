"""Record privileged activity without asking every handler to remember.

The obvious way to build an audit trail is a call in each mutating
handler. This project has learned twice what that produces: F80's product
rename was fixed instance-by-instance and grew 25 new instances, and F88's
service contract drifted to 60% accuracy for the same reason. A list of
call sites is a list, and lists rot.

So coverage is structural. Every mutating request to an authenticated
surface is recorded by this middleware, whether or not the handler knows
it exists. Handlers may *add* to a record — `set_audit_detail(request,
{'role': 'admin'})` — but they cannot forget to create one.

**What counts as auditable** is deliberately a small rule rather than a
route list:

* any `POST`/`PUT`/`PATCH`/`DELETE` under `/v1/portal`, `/v1/admin` or
  `/v1/directory` or `/v1/service-notifications` — every mutation a
  person or a registered service performs — and under `/scim/v2`, an
  identity provider creating and deprovisioning people;
* the authentication endpoints, whatever their method, because a *failed*
  sign-in is among the most valuable things an audit trail holds and is
  the one event with no authenticated actor to attribute it to.

`GET` is not recorded on the first two. That is a judgement, and the
reason is volume against value: the portal issues many reads per page
view, and "who looked at the member list" is not what an auditor
asks. Reads that *do* matter — minting a credential, checking access —
are `POST`s — a service resolving people, telling them something — and
are captured.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.db.sql.audit_events import AuditEventDB, AuditRecord

logger = logging.getLogger(__name__)

_MUTATING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

# Surfaces where a mutation is a privileged act.
_AUDITED_PREFIXES = (
    '/v1/portal',
    '/v1/admin',
    '/v1/directory',
    '/v1/service-notifications',
    '/v1/nodes',
    # Commands as root in producers' VMs, a service asking to reach a
    # producer, and a person approving an MCP server: each a privileged
    # act (docs/service-identity.md §13–§15).
    '/v1/vm',
    '/v1/service-events',
    '/v1/service-items',
    '/v1/service-attachments',
    '/v1/me/mcp-approvals',
    # An identity provider creating, changing, deactivating and deleting
    # people: deprovisioning is the event an auditor samples. Not under
    # `/v1` because the standard fixes the path.
    '/scim/v2',
)

#: Paths a timer calls, which are never events.
#:
#: `/v1/nodes` had to join the prefixes above because registering a node
#: decides *where prompts go*: the registry is idempotent by design, so
#: re-registering an existing id replaces its address, and a caller
#: holding the node token can point a node id at a host they control and
#: receive every chat request routed to it — the prompt, whatever source
#: the CLI attached, the previous turn's tool output. That is the single
#: most consequential unlogged action in the product, and the service
#: contract notes the node token lives in Terraform and cloud-init, "a
#: much wider blast radius" than the service token.
#:
#: But the heartbeat runs every 60 seconds per node. Auditing it would
#: write 1,440 rows per node per day — a twenty-node fleet is 28,800
#: rows of "still alive" daily — which would bury the `denied` rows an
#: administrator actually reads and spend the retention window (DP-6) on
#: pings. **A heartbeat's presence is not interesting and its absence is
#: already handled**, by the registration TTL expiring.
#:
#: Matched as a suffix so this is a rule about timer endpoints rather
#: than a list of node ids. F114.
_TIMER_PATH_SUFFIXES = ('/heartbeat',)

#: Mutations audited wherever they are mounted. The node agent picking up
#: an app's VM credential hands a live credential out of igni server for
#: the only time it ever leaves (docs/service-identity.md §16).
_AUDITED_SUFFIXES = (
    '/vm-credential/pickup',
    # The gateway giving up on events, and a removed member's data
    # leaving through an export (docs/service-identity.md §15, §18.4).
    '/events/fail',
    # An acknowledgement says which events no handler took (§15).
    '/events/ack',
    '/exports/pull',
    '/archive',
    '/failed',
    # An app asking for a VM profile: packages and a script that would
    # run as root once an org admin approves (docs/service-identity.md §12.3).
    '/vm-profile-request',
)

#: Reads audited for who took a copy: an export of a removed member's data.
_AUDITED_READ_SUFFIXES = ('/download',)

# Authentication, recorded regardless of method and outcome.
_AUTH_PATHS = (
    '/v1/sign-in',
    '/v1/sign-up',
    '/v1/sign-in-with-code',
    '/v1/send-sign-in-code',
    '/v1/send-sign-up-code',
    '/v1/send-reset-password-code',
    '/v1/reset-password',
    '/v1/auth',
    '/v1/cli/token',
    # A service or app registering itself. Unauthenticated and
    # deployment-wide, so the record of who asked is the only one.
    '/v1/clients',
)

# Path parameters that name the thing being acted on, most specific first:
# `/portal/groups/{group_id}/members/{user_id}` is an action on the member,
# not on the group.
_TARGET_PARAMS = (
    ('entry_id', 'group_entry'),
    ('user_id', 'user'),
    ('member_id', 'member'),
    ('node_id', 'inference_node'),
    ('group_id', 'group'),
    ('org_id', 'organization'),
)

_STATE_ACTOR = 'audit_actor'
_STATE_DETAIL = 'audit_detail'
_STATE_TARGET = 'audit_target'


class AuditActor(BaseModel):
    """Who the request is attributed to.

    A dict literal built in one place and read with `.get()` in
    another, carried on `request.state` the whole way between — so the
    key set of a compliance record was agreed by two pieces of code
    and written down in neither.
    """

    user_id: str | None = None
    email: str | None = None
    org_id: str | None = None


class AuditTarget(BaseModel):
    """What the request acted on, when the handler says so."""

    #: `target_type`/`target_id`, not `type`/`id`: these go straight
    #: into `AuditRecord` fields of those names, and the dict this
    #: replaces used the short keys at one end and the long ones at
    #: the other.
    target_type: str
    target_id: str


def set_audit_actor(
    request: Request, *, user_id: str | None, email: str | None = None, org_id: str | None = None
) -> None:
    """Attribute this request, from wherever the identity was resolved.

    Called by the authentication dependencies rather than by handlers, so
    every route behind them is attributed without doing anything. Works
    because Starlette backs `request.state` with `scope['state']`, which
    the middleware and the endpoint share — checked before this was built,
    since `BaseHTTPMiddleware` hands the endpoint a different `Request`
    object.
    """
    setattr(request.state, _STATE_ACTOR, AuditActor(user_id=user_id, email=email, org_id=org_id))


def set_audit_target(request: Request, *, target_type: str, target_id: str) -> None:
    """Name the thing this request acted on, when the URL does not.

    `resolve_target` reads path parameters, which covers every route
    whose subject is in its URL. A route that carries its subject in the
    body names it here instead, or its audit row records only a method
    and a path.

    Deliberately *not* "look for an id in the JSON body": the
    middleware runs after the response and must never re-read or buffer
    a request body (F83's reason — a payload can carry personal data —
    and a practical one, the stream is consumed). The handler already
    has the parsed model, so it says.

    An explicit call wins over the path, because a handler that bothers
    to name its subject knows better than the URL does.
    """
    # `setattr` with the constant, rather than
    # `request.state.audit_target = ...`. The constants existed and the
    # writers spelled the attribute out anyway, so the name the reader
    # looks up and the name the writer sets had to agree by hand.
    setattr(request.state, _STATE_TARGET, AuditTarget(target_type=target_type, target_id=target_id))


def set_audit_detail(request: Request, detail: dict[str, Any]) -> None:
    """Add small, deliberate facts to this request's record.

    Never request bodies. F83 is the reason: a payload can carry commit
    messages and author emails, and this table is retained by design, so
    anything put here lives for the whole retention window.
    """
    existing = getattr(request.state, _STATE_DETAIL, None) or {}
    setattr(request.state, _STATE_DETAIL, {**existing, **detail})


# Reads that are audited anyway. The general rule is that mutations are
# recorded and reads are not — a page of groups is not an event. These
# are, for two different reasons.
#
# A prefix match, so `?since=` and any future parameters are covered
# without touching this list.
_AUDITED_READS = (
    # Hands somebody a *copy* of a large amount of data, and the question
    # it answers afterwards is "who took it". F102.
    '/v1/portal/audit-events/export',
    # C10. Not about the data — about the act.
    #
    # AC-8 is "periodic access review", and SOC 2 CC6.3 and ISO A 5.18
    # ask whether somebody *looks* at who still has access. This endpoint
    # made that possible; nothing recorded that it happened. So a Type II
    # auditor asking "show me the access reviews you performed in Q2" had
    # nothing to sample, and the control that exists specifically to be
    # sampled produced no sample.
    #
    # A GET, and the only evidence a review occurred is that somebody
    # made it. Recording the read *is* the record of the review — which
    # is why this is the one class of read where "a page is not an event"
    # is wrong: this page is the event.
    '/v1/portal/access-review',
)


def is_auditable(method: str, path: str) -> bool:
    """The whole rule, in one place and unit-testable without a request."""
    if any(path.startswith(prefix) for prefix in _AUTH_PATHS):
        return True
    if any(path.startswith(prefix) for prefix in _AUDITED_READS):
        return True
    if path.startswith('/v1/portal/orgs/') and path.endswith(_AUDITED_READ_SUFFIXES):
        return True
    if method.upper() not in _MUTATING_METHODS:
        return False
    if path.endswith(_TIMER_PATH_SUFFIXES):
        return False
    if path.endswith(_AUDITED_SUFFIXES):
        return True
    return any(path.startswith(prefix) for prefix in _AUDITED_PREFIXES)


def full_route_template(request_path: str, route_path: str, path_params: dict[str, Any] | None) -> str:
    """The route template including whatever prefix it was mounted under.

    `scope['route'].path` is the path *relative to its own router*, so a
    portal endpoint arrives here as `/groups` — no `/portal`, no `/v1`.
    Stored like that the column is ambiguous (several routers have a
    `/groups`) and it does not match what anybody would search for.

    Reconstructed exactly rather than by string surgery on identifiers:
    render the relative template with the request's own path parameters,
    find that rendering as a suffix of the real request path, and keep
    everything before it as the prefix. If the suffix does not match — a
    route shape this cannot reason about — the real path is returned
    rather than a guess, which is wrong-but-honest instead of
    wrong-and-tidy.
    """
    rendered = route_path
    for name, value in (path_params or {}).items():
        if value is not None:
            rendered = rendered.replace('{' + name + '}', str(value))

    if rendered and request_path.endswith(rendered):
        prefix = request_path[: -len(rendered)]
        return f'{prefix}{route_path}'
    return request_path


def resolve_target(path_params: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Which entity this acted on, from the route's own parameters.

    Ordered most-specific-first so a nested route attributes to the inner
    entity: removing a member from a group is an action on the member.
    """
    if not path_params:
        return None, None
    for name, target_type in _TARGET_PARAMS:
        if name in path_params and path_params[name] is not None:
            return target_type, str(path_params[name])
    return None, None


class AuditMiddleware(BaseHTTPMiddleware):
    """Append one audit event per privileged request."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._db = AuditEventDB()

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)

        # Deliberately after the response and outside any `raise`: an
        # audit row must not change what the caller receives. `record`
        # itself never raises, and this guard covers the rest.
        try:
            if is_auditable(request.method, request.url.path):
                await self._write(request, response.status_code)
        except Exception:
            logger.exception('AUDIT MIDDLEWARE FAILED for %s %s', request.method, request.url.path)

        return response

    async def _write(self, request: Request, status_code: int) -> None:
        actor = getattr(request.state, _STATE_ACTOR, None) or AuditActor()
        path_params = request.scope.get('path_params')
        relative = getattr(request.scope.get('route'), 'path', None)
        route = full_route_template(request.url.path, relative, path_params) if relative else request.url.path
        target_type, target_id = resolve_target(path_params)
        stated = getattr(request.state, _STATE_TARGET, None)
        if stated:
            target_type, target_id = stated.target_type, stated.target_id

        await self._db.record(
            AuditRecord(
                method=request.method,
                route=route,
                status_code=status_code,
                actor_user_id=actor.user_id,
                actor_email=actor.email,
                org_id=actor.org_id,
                target_type=target_type,
                target_id=target_id,
                client_host=request.client.host if request.client else None,
                # Recorded as the claim it is — see the model's comment.
                forwarded_for=request.headers.get('x-forwarded-for'),
                user_agent=request.headers.get('user-agent'),
                detail=getattr(request.state, _STATE_DETAIL, None),
            )
        )
