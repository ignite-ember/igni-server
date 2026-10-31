"""`GET /v1/revocations`: what a service must stop accepting (§21); and the organisations it serves.

A service checks people's, apps' and spaces' tokens by their signature;
this is how it learns, within a second, of one revoked before it
expires. Its own service token (§3) — the credential it already holds —
names the organisation; it gets that organisation's revocations and
those for every organisation — or, with `scope=served`, those of every
organisation its extension is on in, with one cursor, on one long poll. `ETag` is the cursor: asked with
`If-None-Match` and nothing new, 304.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.api.base_v1.schema.producer_feed import RevocationPage
from app.api.base_v1.schema.served import ServedOrganisations
from app.security.service_caller import require_service_follower, ServiceFollower
from app.services.cloud.revocations import Revocations
from app.services.identity.served_organisations import Served

router = APIRouter()


@router.get('/organisations/served', response_model=ServedOrganisations, tags=['Identity'])
async def served(caller: ServiceFollower = Depends(require_service_follower)) -> ServedOrganisations:
    """Every organisation with this service's extension on — any of its tokens, or one for revocations."""
    return await Served().list(caller.client_id)


@router.get('/revocations', response_model=RevocationPage, tags=['Identity'])
async def revocations(
    response: Response,
    since: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=500, ge=1, le=1000),
    wait: float = Query(default=0, ge=0, le=30),
    scope: Literal['token', 'served'] = Query(
        default='token', description="`token`: the token's organisation; `served`: every one the service serves"
    ),
    if_none_match: str | None = Header(default=None),
    caller: ServiceFollower = Depends(require_service_follower),
) -> RevocationPage | Response:
    if scope == 'token' and caller.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='This token names no organisation: ask with scope=served',
        )
    org_ids = [caller.org_id] if scope == 'token' and caller.org_id else await Served().org_ids(caller.client_id)
    page = await Revocations().page(since, org_ids=org_ids, limit=limit, wait=wait)
    etag = f'"{page.cursor}"'
    if not page.revocations and if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={'ETag': etag})
    response.headers['ETag'] = etag
    response.headers['Cache-Control'] = 'private, no-cache'
    return page
