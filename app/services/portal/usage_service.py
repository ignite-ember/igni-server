"""Service layer for portal usage analytics."""

from __future__ import annotations

from typing import Literal

from app.api.base_v1.schema.portal.usage import UsageDataPoint, UsageResponse
from app.api.base_v1.schema.portal.user import PortalUser
from app.db.sql.chat_usage import ChatUsageDB


class UsageService:
    """Daily and total chat usage, for the portal's charts."""

    def __init__(self, *, usage_db: ChatUsageDB | None = None) -> None:
        # Injected rather than read from a module-level instance, which
        # is what made this untestable without patching the module.
        self._usage = usage_db or ChatUsageDB()

    async def get_usage(
        self,
        current_user: PortalUser,
        *,
        days: int,
        scope: Literal['personal', 'team'] = 'personal',
    ) -> UsageResponse:
        """Return daily-aggregated chat usage.

        ``personal`` filters by the current user; ``team`` returns
        org-wide totals.

        **Authorisation is the endpoint's, and that is worth stating
        rather than assuming.** Whether somebody may read org-wide
        totals is decided before this is called, so a second caller
        reaching for `scope='team'` gets team data with no check. There
        is one caller today; a second needs the check moved, not
        repeated.
        """
        org_id = current_user.org_id
        user_ids = [current_user.user_id] if scope == 'personal' else None

        daily = await self._usage.get_daily_usage(org_id=org_id, days=days, user_ids=user_ids)
        totals = await self._usage.get_totals(org_id=org_id, days=days, user_ids=user_ids)

        return UsageResponse(
            data=[UsageDataPoint(**point.model_dump()) for point in daily],
            total_tokens=totals.total_tokens,
            total_input_tokens=totals.input_tokens,
            total_output_tokens=totals.output_tokens,
            total_requests=totals.request_count,
        )
