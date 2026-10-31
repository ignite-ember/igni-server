"""The gateway's feed of new and changed producers (`docs/service-identity.md` §11.3).

A platform token only. The gateway pages through it with the cursor it
was last given, and provisions each producer that appears or whose
profile version changed.
"""

from fastapi import APIRouter, Depends, Query

from app.api.base_v1.schema.producer_feed import ProducerFeedPage, RevocationPage
from app.security.platform_caller import PlatformCaller, require_platform
from app.services.cloud.producer_feed import ProducerFeed
from app.services.cloud.revocations import Revocations

router = APIRouter()


@router.get('/producers', response_model=ProducerFeedPage)
async def producers(
    since: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=500, ge=1, le=1000),
    wait: float = Query(default=0, ge=0, le=30, description='Seconds to wait for a change when there is none'),
    _platform: PlatformCaller = Depends(require_platform),
) -> ProducerFeedPage:
    return await ProducerFeed().page(since, limit=limit, wait=wait)


@router.get('/revocations', response_model=RevocationPage)
async def revocations(
    since: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=500, ge=1, le=1000),
    wait: float = Query(default=0, ge=0, le=30),
    _platform: PlatformCaller = Depends(require_platform),
) -> RevocationPage:
    """Every revocation, for the gateway (§21)."""
    return await Revocations().page(since, org_ids=None, limit=limit, wait=wait)
