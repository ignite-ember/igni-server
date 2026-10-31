"""``GET /metrics``, and the reason it is not open.

Empty ``METRICS_TOKEN`` means the endpoint does not exist — 404, not 401,
and not an unauthenticated 200. The same shape as an unset
``NODE_REGISTRATION_TOKEN`` disabling node registration: a deployment that has not thought about this
gets nothing rather than something.

Open metrics are not harmless. The exposition lists every route template
in the product, the version each replica is running, and the rate of
everything — which is a map for somebody deciding what to attack and, on
a busy deployment, a rough business metric. Prometheus supports a bearer
token per scrape target; there is no reason to publish this instead.

404 rather than 401 when disabled is deliberate: an unconfigured
deployment should not advertise that a metrics endpoint exists here at
all.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, Response, status

from app.config import settings
from app.monitoring.metrics import render

router = APIRouter()


@router.get('/metrics', include_in_schema=False)
async def metrics(authorization: str | None = Header(default=None)) -> Response:
    if not settings.metrics_token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    presented = (authorization or '').removeprefix('Bearer ').strip()
    # compare_digest on the raw strings: a missing header compares against
    # the token and loses, in the same time as a wrong one.
    if not hmac.compare_digest(presented, settings.metrics_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid metrics token')

    return Response(content=render(), media_type='text/plain; version=0.0.4; charset=utf-8')
