"""The probes: liveness, readiness and version, mounted at the app root.

Root-mounted on purpose: an orchestrator's liveness check should not move
when the API version does.
"""

from fastapi import APIRouter, Response, status

from app.config import settings
from app.services.readiness import LivenessReport, ReadinessProbe, ReadinessReport, VersionReport

router = APIRouter(tags=['Health'])


@router.get('/health', response_model=LivenessReport)
async def health_check() -> LivenessReport:
    """Liveness: this process is running and serving.

    Deliberately checks nothing. A liveness probe that fails when the
    database is unreachable gets the container killed and restarted for a
    problem a restart cannot fix, turning a database blip into a restart
    loop. Use ``/ready`` to decide whether to send traffic.
    """
    return LivenessReport(status='ok')


@router.get('/ready', response_model=ReadinessReport)
async def readiness_check(response: Response) -> ReadinessReport:
    """Readiness: this process can actually serve a request.

    503 when a dependency is down, so an orchestrator can drain the
    instance without killing it. The checks are the cheapest possible
    round trip to each — ``SELECT 1`` and a ``PING`` — because this
    endpoint gets polled.

    **A busy pool is not an unready process.** The check shares the
    application's connection pool, so it cannot be insulated from the
    exhaustion it exists to detect; but under a burst it would then queue,
    time out and get the pod pulled out of service, sending its traffic to
    a sibling replica that saturates in turn.

    So saturation and unavailability are answered differently. The check
    waits a short, fixed time; if it does not get a connection and the
    pool is full, that is ``busy`` and the process stays **ready**,
    because removing a working replica from a load balancer is the one
    action guaranteed to make a capacity problem worse. Anything else —
    the server refusing, the host unreachable, a timeout with the pool
    *not* full — is ``unavailable``, and that is a 503. F116.
    """
    report = await ReadinessProbe().run()
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report


@router.get('/version', response_model=VersionReport)
async def version() -> VersionReport:
    return VersionReport(version=settings.app_version)
