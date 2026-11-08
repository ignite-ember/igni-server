"""Which requests are auditable: a small rule rather than a route list.

* any `POST`/`PUT`/`PATCH`/`DELETE` under an authenticated surface —
  every mutation a person or a registered service performs — and under
  `/scim/v2`, an identity provider creating and deprovisioning people;
* the authentication endpoints, whatever their method, because a *failed*
  sign-in is among the most valuable things an audit trail holds;
* a short list of reads that are events in themselves.

`GET` is otherwise not recorded: the portal issues many reads per page
view, and "who looked at the member list" is not what an auditor asks.
"""

_MUTATING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

# Surfaces where a mutation is a privileged act.
_AUDITED_PREFIXES = (
    '/v1/portal',
    '/v1/admin',
    '/v1/directory',
    '/v1/service-notifications',
    # Registering a node decides where prompts go, and re-registering an
    # id replaces its address (F114).
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
    # people. Not under `/v1` because the standard fixes the path.
    '/scim/v2',
)

#: Paths a timer calls, which are never events. A node heartbeats every
#: 60 seconds — 1,440 rows per node per day that would bury the rows an
#: administrator reads — and its absence is already handled by the
#: registration TTL. A suffix, so it is a rule about timer endpoints
#: rather than a list of node ids (F114).
_TIMER_PATH_SUFFIXES = ('/heartbeat',)

#: Mutations audited wherever they are mounted.
_AUDITED_SUFFIXES = (
    # A live VM credential leaving igni server, its only time (§16).
    '/vm-credential/pickup',
    # The gateway giving up on events, which events no handler took, and
    # a removed member's data leaving through an export (§15, §18.4).
    '/events/fail',
    '/events/ack',
    '/exports/pull',
    '/archive',
    '/failed',
    # Packages and a script that would run as root once approved (§12.3).
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

# Reads that are events. Prefixes, so query parameters are covered.
_AUDITED_READS = (
    # A copy of the whole trail; the question afterwards is who took it (F102).
    '/v1/portal/audit-events/export',
    # Performing an access review *is* the control (AC-8, SOC 2 CC6.3,
    # ISO A 5.18): recording the read is the evidence it happened (C10).
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
