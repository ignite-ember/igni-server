"""Counts 5xx responses by route template.

A separate middleware from the audit one, and it sits further out
deliberately: the audit middleware records what a *user* did, so it only
looks at the routes F90's policy covers. A 500 is worth counting wherever
it happens, including on routes nobody audits.

The label is the route *template*, reusing the audit middleware's
reconstruction of it. Two reasons, and the second is the one that bites:
a raw path makes every `/v1/portal/groups/<uuid>` its own time series
until the monitoring falls over, and a raw path is also personal data as
soon as an identifier in it belongs to somebody.

Unmatched requests — a scanner hitting `/wp-login.php` — collapse to
`unmatched` rather than being counted per path, for the same reason.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.middlewares.audit import full_route_template
from app.monitoring import HTTP_DURATION, HTTP_REQUESTS, SERVER_ERRORS


def _route_label(request: Request) -> str:
    """The route template, or `unmatched` — never the request path.

    `scope['route'].path` is relative to the router it was declared on, so
    it needs the mount prefix put back; `full_route_template` is the
    audit middleware's reconstruction of exactly that, reused rather than
    reimplemented one subtle bug differently.
    """
    relative = getattr(request.scope.get('route'), 'path', None)
    if not relative:
        return 'unmatched'
    return full_route_template(request.url.path, relative, request.scope.get('path_params'))


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started

        route = _route_label(request)

        # Timed even when unmatched, because a flood of 404s from a
        # scanner is worth seeing in the same place as everything else.
        HTTP_DURATION.labels(route=route).observe(elapsed)
        HTTP_REQUESTS.labels(
            method=request.method,
            route=route,
            # The class, not the code. `503` and `500` are the same page
            # at 3am, and forty label values are not.
            status=f'{response.status_code // 100}xx',
        ).inc()

        if response.status_code >= 500:
            SERVER_ERRORS.labels(route=route).inc()

        return response
