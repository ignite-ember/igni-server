"""`/v1/me/cloud`: whether a person has a cloud igni to use from the desktop, and who pulls their events.

The desktop's Local / Cloud switch is built from this answer (igni-core
`docs/design/local-cloud-switch.md` §2.1): the shell asks at launch, after
signing in or out, every five minutes while the app is focused, and at
once when the gateway closes with a reason. The person's own session
only — an app, a space or a VM is refused, because this describes a
person's desktop.

Revalidates: `ETag` and `Cache-Control: private, max-age=60`, so a shell
asking again within the minute, or with the tag it holds, costs nothing.
"""

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.base_v1.schema.audit_detail import Change, EventsOwnerSetAudit
from app.api.base_v1.schema.cloud_discovery import CloudDiscovery, SetEventsOwner
from app.middlewares.request_audit import RequestAudit
from app.security.producer import ProducerCaller, require_desktop_person
from app.services.cloud.cloud_discovery import CloudDiscoveries
from app.services.cloud.events_owner_errors import EventsOwnerRefused
from app.services.cloud.events_owner_refusals import EventsOwnerRefusals
from app.services.deployment_licence.service import deployment_licence, DeploymentLicence

router = APIRouter(prefix='/me/cloud')

#: The shell asks every five minutes; a minute's reuse spares the repeats in between.
CACHE_CONTROL = 'private, max-age=60'


def _answer(discovery: CloudDiscovery, *, held_etag: str | None) -> Response:
    etag = discovery.etag()
    headers = {'ETag': etag, 'Cache-Control': CACHE_CONTROL, 'Vary': 'Authorization'}
    if held_etag == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return JSONResponse(content=discovery.model_dump(mode='json'), headers=headers)


@router.get('', response_model=CloudDiscovery, responses={304: {'description': 'Unchanged since the ETag sent'}})
async def my_cloud(
    request: Request,
    caller: ProducerCaller = Depends(require_desktop_person),
    licence: DeploymentLicence = Depends(deployment_licence),
) -> Response:
    """Whether there is a cloud igni for this person, its gateway, their group's mode and their events' owner."""
    discovery = await CloudDiscoveries(licence).of(caller)
    return _answer(discovery, held_etag=request.headers.get('if-none-match'))


@router.put(
    '/events-owner',
    response_model=CloudDiscovery,
    responses={409: {'description': '`{"error": "mode_forbids"}`: the group runs igni in the other place only'}},
)
async def set_events_owner(
    request: Request,
    body: SetEventsOwner,
    caller: ProducerCaller = Depends(require_desktop_person),
    licence: DeploymentLicence = Depends(deployment_licence),
) -> Response:
    """Which side pulls this person's events from now on: `local` or `cloud`. Audited.

    The desktop sets it before it re-points its windows, so the side it
    switches to is the one served. Leases are left alone: what the other
    side leased and did not acknowledge comes back after the lease.
    """
    try:
        changed = await CloudDiscoveries(licence).set_events_owner(caller, body.owner)
    except EventsOwnerRefused as exc:
        return EventsOwnerRefusals.response(exc)
    RequestAudit.of(request).add_detail(
        EventsOwnerSetAudit(events_owner=Change(was=changed.owner_was, now=changed.discovery.events_owner))
    )
    return _answer(changed.discovery, held_etag=None)
