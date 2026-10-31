"""Database layer for chat usage logging and aggregation.

In each organisation's own database. A request from somebody in no
organisation leaves no row: there is no organisation to count it
against, and none whose portal would show it.
"""

from datetime import date, datetime, timedelta, timezone
import logging

from sqlalchemy import cast, Date, func, select

from app.db.sql.models.chat_usage import ChatUsage
from app.db.sql.schemas.chat_usage import DailyUsage, UsageTotals
from app.db.sql.tenancy import TenantData

logger = logging.getLogger(__name__)


class ChatUsageDB(TenantData):
    """Read/write operations for chat_usage table."""

    async def log(
        self,
        *,
        user_id: str,
        org_id: str | None,
        model: str,
        app_id: str | None = None,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        duration: float | None,
        stream: bool,
    ) -> None:
        """Insert a single usage row. Fire-and-forget safe."""
        if org_id is None:
            return
        async with self.session_scope(org_id) as session:
            row = ChatUsage(
                user_id=user_id,
                org_id=org_id,
                app_id=app_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                duration=duration,
                stream=1 if stream else 0,
            )
            session.add(row)

    async def get_daily_usage(
        self,
        *,
        org_id: str,
        days: int = 30,
        user_ids: list[str] | None = None,
    ) -> list[DailyUsage]:
        """Return daily-aggregated usage for the given org over the last N days."""
        since = datetime.now(timezone.utc) - timedelta(days=days)

        async with self.session_scope(org_id) as session:
            q = select(
                cast(ChatUsage.created_at, Date).label('date'),
                func.sum(ChatUsage.input_tokens).label('input_tokens'),
                func.sum(ChatUsage.output_tokens).label('output_tokens'),
                func.sum(ChatUsage.total_tokens).label('total_tokens'),
                func.count().label('request_count'),
            ).where(ChatUsage.org_id == org_id, ChatUsage.created_at >= since)

            if user_ids:
                q = q.where(ChatUsage.user_id.in_(user_ids))

            q = q.group_by(cast(ChatUsage.created_at, Date)).order_by(cast(ChatUsage.created_at, Date))

            rows = (await session.execute(q)).all()

            return [
                DailyUsage(
                    date=r.date.isoformat() if isinstance(r.date, date) else str(r.date),
                    input_tokens=int(r.input_tokens),
                    output_tokens=int(r.output_tokens),
                    total_tokens=int(r.total_tokens),
                    request_count=int(r.request_count),
                )
                for r in rows
            ]

    async def get_totals(
        self,
        *,
        org_id: str,
        days: int = 30,
        user_ids: list[str] | None = None,
    ) -> UsageTotals:
        """Return aggregate totals for the given org over the last N days."""
        since = datetime.now(timezone.utc) - timedelta(days=days)

        async with self.session_scope(org_id) as session:
            q = select(
                func.coalesce(func.sum(ChatUsage.input_tokens), 0).label('input_tokens'),
                func.coalesce(func.sum(ChatUsage.output_tokens), 0).label('output_tokens'),
                func.coalesce(func.sum(ChatUsage.total_tokens), 0).label('total_tokens'),
                func.count().label('request_count'),
            ).where(ChatUsage.org_id == org_id, ChatUsage.created_at >= since)

            if user_ids:
                q = q.where(ChatUsage.user_id.in_(user_ids))

            r = (await session.execute(q)).one()

            return UsageTotals(
                input_tokens=int(r.input_tokens),
                output_tokens=int(r.output_tokens),
                total_tokens=int(r.total_tokens),
                request_count=int(r.request_count),
            )
