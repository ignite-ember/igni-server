"""The SCIM 2.0 Users resource (RFC 7644).

Six routes, which is what a provider actually calls: look somebody up
before creating them, create, read, change, replace, remove. Plus
``ServiceProviderConfig``, which every provider fetches first to find
out what it is talking to.

**Not under ``/v1``.** The path is fixed by the standard — a provider
is configured with a base URL and appends ``/Users`` itself — so it
is not ours to version. ``/metrics`` is mounted beside it for the same
reason.

**Groups are not implemented**, deliberately and visibly: the
``ServiceProviderConfig`` below says so, and a provider configured to
push groups will report that rather than silently syncing nothing.
Group membership here means an organisation and a role, which is not
a thing a directory should be able to set until somebody decides how
the two map.

Every route is guarded by :func:`require_scim_token`, and the whole
API answers 503 when that token is unset, so a deployment that has
not configured SCIM is not quietly running an unauthenticated
provisioning endpoint.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.base_v1.schema.scim import (
    ListResponse,
    PatchRequest,
    ScimUser,
)
from app.api.scim.errors import scim_error
from app.security.scim_token import require_scim_token
from app.services.scim import service

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_scim_token)])

#: ``userName eq "someone@example.com"`` — the only filter a provider
#: sends when it is looking somebody up before creating them, which is
#: the only filter this needs to answer. Anything else is refused as
#: an invalid filter rather than silently matching everything, which
#: would hand a provider the whole directory.
_FILTER = re.compile(r'^\s*userName\s+eq\s+"([^"]*)"\s*$', re.IGNORECASE)


@router.get(
    '/ServiceProviderConfig',
    description='What this SCIM implementation supports. Providers read it first.',
)
async def service_provider_config() -> dict:
    return {
        'schemas': ['urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig'],
        # No `documentationUri`. It is optional, and the one URL worth
        # putting there is the vendor's — which is a host named in
        # `app/` for a convenience link, on a product whose rule is
        # that there are none. The operator reads how to configure
        # this where they configure it: `dist.env` and the chart.
        'patch': {'supported': True},
        # Said plainly rather than left for a provider to discover by
        # having a run fail.
        'bulk': {'supported': False, 'maxOperations': 0, 'maxPayloadSize': 0},
        'filter': {'supported': True, 'maxResults': 200},
        'changePassword': {'supported': False},
        'sort': {'supported': False},
        'etag': {'supported': False},
        'authenticationSchemes': [
            {
                'type': 'oauthbearertoken',
                'name': 'OAuth Bearer Token',
                'description': 'A token configured on both sides; see scim_token.',
                'primary': True,
            }
        ],
    }


@router.get('/Users', response_model=ListResponse, description='Find a user, by address.')
async def list_users(
    filter: str = Query(default='', alias='filter'),
    startIndex: int = Query(default=1, ge=1),
    count: int = Query(default=100, ge=0, le=200),
) -> ListResponse:
    """Answer a provider's "do you already have this person".

    An unfiltered listing answers **empty** rather than the whole
    directory. A provider asks this to decide whether to create
    somebody, and no provider needs every account to do it; handing
    over the staff list to answer a question nobody asked would be the
    wrong default for a self-hosted product.
    """
    if not filter:
        return ListResponse(totalResults=0, startIndex=startIndex, itemsPerPage=0, Resources=[])

    match = _FILTER.match(filter)
    if not match:
        raise scim_error(f'Unsupported filter: {filter}', status.HTTP_400_BAD_REQUEST, 'invalidFilter')

    user = await service.find_by_address(match.group(1).strip().lower())
    found = [service.to_scim(user)] if user is not None else []
    return ListResponse(
        totalResults=len(found),
        startIndex=startIndex,
        itemsPerPage=len(found) if count else 0,
        Resources=found if count else [],
    )


@router.post(
    '/Users',
    response_model=ScimUser,
    status_code=status.HTTP_201_CREATED,
    description='Provision a user.',
)
async def create_user(payload: ScimUser) -> ScimUser:
    address = service.address_of(payload)
    if not address:
        raise scim_error('userName is required', status.HTTP_400_BAD_REQUEST, 'invalidValue')

    existing = await service.find_by_address(address)
    if existing is not None:
        # 409 with `uniqueness`, which is what tells a provider to stop
        # retrying and reconcile instead. A 400 here makes a sync loop.
        raise scim_error(f'{address} already exists', status.HTTP_409_CONFLICT, 'uniqueness')

    return service.to_scim(await service.provision(payload))


@router.get('/Users/{user_id}', response_model=ScimUser, description='Read one user.')
async def read_user(user_id: str) -> ScimUser:
    user = await service.find_by_id(user_id)
    if user is None:
        raise scim_error('User not found', status.HTTP_404_NOT_FOUND)
    return service.to_scim(user)


@router.patch('/Users/{user_id}', response_model=ScimUser, description='Change one user.')
async def patch_user(user_id: str, payload: PatchRequest) -> ScimUser:
    """The route deprovisioning actually arrives on.

    A patch that says nothing about ``active`` is accepted and changes
    nothing: providers sync display names and other attributes this
    server does not store, and failing those would make an
    administrator investigate a provisioning error that is not one.
    """
    user = await service.find_by_id(user_id)
    if user is None:
        raise scim_error('User not found', status.HTTP_404_NOT_FOUND)

    active = service.wants_deactivation(payload.Operations)
    if active is None:
        return service.to_scim(user)
    return service.to_scim(await service.set_active(user_id, active=active))


@router.put('/Users/{user_id}', response_model=ScimUser, description='Replace one user.')
async def replace_user(user_id: str, payload: ScimUser) -> ScimUser:
    """Some providers replace rather than patch.

    Only ``active`` is applied. A replace carrying a different address
    is **not** a rename here: an account is keyed on its address and
    every other sign-in path agrees, so honouring it would move
    somebody's identity out from under their documents.
    """
    user = await service.find_by_id(user_id)
    if user is None:
        raise scim_error('User not found', status.HTTP_404_NOT_FOUND)

    if payload.active == (getattr(user, 'deactivated_at', None) is None):
        return service.to_scim(user)
    return service.to_scim(await service.set_active(user_id, active=payload.active))


@router.delete(
    '/Users/{user_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    description='Deprovision a user. Deactivates; does not delete their work.',
)
async def delete_user(user_id: str) -> Response:
    """Treated as deactivation, which is the honest reading.

    Providers differ about whether removing somebody sends ``DELETE``
    or sets ``active: false``, and the two must mean the same thing
    here: a provider's choice of verb should not decide whether a
    departed colleague's documents survive. Removing the row would
    take their work and everything built on it, which no directory
    operation should do silently.
    """
    user = await service.find_by_id(user_id)
    if user is None:
        raise scim_error('User not found', status.HTTP_404_NOT_FOUND)
    await service.set_active(user_id, active=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
