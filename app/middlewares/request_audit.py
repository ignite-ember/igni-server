"""What one request's audit row will say, gathered while the request runs.

The audit middleware writes the row after the response; the facts in it
come from whoever learns them first. The authentication dependencies say
who the caller is, a handler names what it acted on and adds small,
deliberate facts. They all write to one `RequestAudit` kept on
`request.state`, which Starlette backs with `scope['state']` — shared by
the middleware and the endpoint although `BaseHTTPMiddleware` hands the
endpoint a different `Request` object.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field
from starlette.requests import Request

#: The one `request.state` attribute the record lives under.
_STATE_ATTRIBUTE = 'audit'

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


class AuditActor(BaseModel):
    """Who the request is attributed to."""

    user_id: str | None = None
    email: str | None = None
    org_id: str | None = None


class AuditTarget(BaseModel):
    """What the request acted on.

    `target_type`/`target_id`, not `type`/`id`: these go straight into
    `AuditRecord` fields of those names.
    """

    target_type: str
    target_id: str

    @classmethod
    def from_path_params(cls, path_params: Mapping[str, object] | None) -> AuditTarget | None:
        """The entity named by the route's own parameters, innermost first.

        Removing a member from a group is an action on the member, so a
        nested route attributes to the inner entity.
        """
        if not path_params:
            return None
        for name, target_type in _TARGET_PARAMS:
            value = path_params.get(name)
            if value is not None:
                return cls(target_type=target_type, target_id=str(value))
        return None


class RequestAudit(BaseModel):
    """The facts this request's audit row will carry."""

    actor: AuditActor = Field(default_factory=AuditActor)
    target: AuditTarget | None = None
    #: JSON per route, so its keys are not one known set: each fact
    #: arrives as a typed model through `add_detail` and is stored dumped.
    detail: dict[str, Any] | None = None
    #: Record this request whatever its path.
    forced: bool = False

    @classmethod
    def of(cls, request: Request) -> RequestAudit:
        """This request's record, started on first use."""
        audit = getattr(request.state, _STATE_ATTRIBUTE, None)
        if audit is None:
            audit = cls()
            setattr(request.state, _STATE_ATTRIBUTE, audit)
        return audit

    def attribute_to(self, *, user_id: str | None, email: str | None = None, org_id: str | None = None) -> None:
        """Attribute this request, from wherever the identity was resolved.

        Called by the authentication dependencies rather than by handlers,
        so every route behind them is attributed without doing anything.
        """
        self.actor = AuditActor(user_id=user_id, email=email, org_id=org_id)

    def set_target(self, *, target_type: str, target_id: str) -> None:
        """Name the thing this request acted on, when the URL does not.

        Wins over the path parameters: a handler that names its subject
        knows better than the URL. Never "look for an id in the JSON
        body": the middleware runs after the response and must not re-read
        a request body (F83 — a payload can carry personal data).
        """
        self.target = AuditTarget(target_type=target_type, target_id=target_id)

    def record_regardless(self) -> None:
        """Record this request although its path is not one the policy names.

        For a route shared by things worth a row and things not: the token
        endpoint records an API key's exchange, not every service's token.
        """
        self.forced = True

    def add_detail(self, facts: BaseModel) -> None:
        """Add small, deliberate facts to this request's record.

        Never request bodies (F83): this table is retained by design, so
        anything put here lives for the whole retention window.
        """
        self.detail = {**(self.detail or {}), **facts.model_dump(mode='json')}

    def target_or(self, path_params: Mapping[str, object] | None) -> AuditTarget | None:
        """The stated target, else the one the route's parameters name."""
        return self.target or AuditTarget.from_path_params(path_params)
