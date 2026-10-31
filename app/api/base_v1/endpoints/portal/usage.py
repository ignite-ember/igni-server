"""Portal usage endpoints — server-side chat usage analytics."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.base_v1.schema.portal.usage import UsageResponse
from app.api.base_v1.schema.portal.user import PortalUser
from app.security.portal_auth import get_current_portal_user
from app.services.portal.usage_service import UsageService

router = APIRouter()


@router.get(
    '/usage',
    response_model=UsageResponse,
    status_code=status.HTTP_200_OK,
    description='Get aggregated chat usage data for charts',
)
async def get_usage(
    days: int = Query(30, ge=1, le=365),
    scope: Literal['personal', 'team'] = Query('personal'),
    current_user: PortalUser = Depends(get_current_portal_user),
) -> UsageResponse:
    """Return daily-aggregated chat usage.

    ``scope='personal'`` returns usage for the current user only.
    ``scope='team'`` returns the whole org and requires admin role —
    non-admins requesting team scope get a 403.
    """
    if scope == 'team' and current_user.org_role != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Only org admins can view team-wide usage',
        )
    return await UsageService().get_usage(current_user, days=days, scope=scope)
