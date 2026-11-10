"""Record privileged activity without asking every handler to remember.

The obvious way to build an audit trail is a call in each mutating
handler. This project has learned twice what that produces: F80's product
rename was fixed instance-by-instance and grew 25 new instances, and F88's
service contract drifted to 60% accuracy for the same reason. A list of
call sites is a list, and lists rot.

So coverage is structural. Every request `is_auditable` names is recorded
by this middleware, whether or not the handler knows it exists. Handlers
may *add* to a record through `RequestAudit` but they cannot forget to
create one.
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.db.sql.audit_events import AuditEventDB, AuditRecord
from app.middlewares.audit_policy import is_auditable
from app.middlewares.request_audit import RequestAudit
from app.middlewares.route_template import full_route_template

logger = logging.getLogger(__name__)


class AuditMiddleware(BaseHTTPMiddleware):
    """Append one audit event per privileged request."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._db = AuditEventDB()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        # Deliberately after the response and outside any `raise`: an
        # audit row must not change what the caller receives. `record`
        # itself never raises, and this guard covers the rest.
        try:
            audit = RequestAudit.of(request)
            if audit.forced or is_auditable(request.method, request.url.path):
                await self._write(request, audit, response.status_code)
        except Exception:
            logger.exception('AUDIT MIDDLEWARE FAILED for %s %s', request.method, request.url.path)

        return response

    async def _write(self, request: Request, audit: RequestAudit, status_code: int) -> None:
        path_params = request.scope.get('path_params')
        relative = getattr(request.scope.get('route'), 'path', None)
        route = full_route_template(request.url.path, relative, path_params) if relative else request.url.path
        target = audit.target_or(path_params)

        await self._db.record(
            AuditRecord(
                method=request.method,
                route=route,
                status_code=status_code,
                actor_user_id=audit.actor.user_id,
                actor_email=audit.actor.email,
                org_id=audit.actor.org_id,
                target_type=target.target_type if target else None,
                target_id=target.target_id if target else None,
                client_host=request.client.host if request.client else None,
                # Recorded as the claim it is — see the model's comment.
                forwarded_for=request.headers.get('x-forwarded-for'),
                user_agent=request.headers.get('user-agent'),
                detail=audit.detail,
            )
        )
