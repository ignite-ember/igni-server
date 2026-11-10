"""Telling every approved service that an organisation was deleted.

The deletion writes the outbox (`OrganisationDeletionNoticeDB.owe`, in the
deletion's own transaction): one notice per approved service. This
delivers it:

* `DELETE {base_url}/v1/igni/organisations/{org_id}`, with a token igni
  signs for that one service (`aud` = its URL, `token_use` = `igni`,
  `org` = the organisation) — the same kind of call as erasure.
* A 2xx confirms, and the notice goes. Anything else — refused, 501,
  timed out, unreachable — leaves it, to be tried again after
  `retry_delay(attempts)`: 30 s doubling to at most six hours, for as long
  as the service stays registered. A deletion is never "done enough"
  while a service still routes to the organisation's data.

A round runs in every replica, every `INTERVAL_SECONDS`, and right away
after a deletion in this replica (`DeletionWake.nudge`). Replicas do not take the same
notice twice: `take_due` leases rows with `SKIP LOCKED`, and a replica
that dies holding one lets it go when the lease runs out. That is why
this needs no advisory lock — unlike retention, the work divides by row.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import logging

import httpx
from pydantic import BaseModel, Field

from app.db.sql.organisation_deletion_notices import DueNotice, OrganisationDeletionNoticeDB
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.auth.token_claims import CustomClaims
from app.services.identity.organisation_deletion_wake import DeletionWake, ProcessDeletionWake

logger = logging.getLogger(__name__)

#: Long enough for one call, and no use to anybody afterwards.
CALL_TOKEN_LIFETIME = timedelta(minutes=2)
TIMEOUT_SECONDS = 15.0
#: How long a taken notice is this replica's. Longer than one call can take.
LEASE = timedelta(minutes=5)
#: Notices per round; the rest wait for the next.
BATCH = 100
FIRST_RETRY = timedelta(seconds=30)
LONGEST_RETRY = timedelta(hours=6)
INTERVAL_SECONDS = 30.0


class DeliveredNotice(BaseModel):
    """One notice a round called a service about."""

    org_id: str
    service_name: str

    @classmethod
    def of(cls, notice: DueNotice) -> DeliveredNotice:
        return cls(org_id=notice.org_id, service_name=notice.service_name)


class DeliveryRound(BaseModel):
    """What one round did."""

    confirmed: list[DeliveredNotice] = Field(default_factory=list)
    unconfirmed: list[DeliveredNotice] = Field(default_factory=list)


class OrganisationDeletions:
    """Delivers the outbox; also the background loop that keeps doing it."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        tokens: JWTService | None = None,
        notices: OrganisationDeletionNoticeDB | None = None,
        wake: DeletionWake | None = None,
    ) -> None:
        self._http = http
        self._tokens = tokens or JWTService()
        self._notices = notices or OrganisationDeletionNoticeDB()
        self._wake = wake or ProcessDeletionWake()
        self._task: asyncio.Task[None] | None = None

    @staticmethod
    def retry_delay(attempts: int) -> timedelta:
        """After `attempts` unconfirmed calls: 30 s, 1 min, 2 min … six hours at most."""
        delay: timedelta = FIRST_RETRY * (2 ** min(attempts, 20))
        return min(delay, LONGEST_RETRY)

    async def deliver_due(self, *, now: datetime | None = None) -> DeliveryRound:
        """Call every service owed a notice now, once. Safe to run anywhere, any time."""
        now = now or datetime.now(timezone.utc)
        due = await self._notices.take_due(now=now, lease=LEASE, limit=BATCH)
        delivered = DeliveryRound()
        if not due:
            return delivered
        client = self._http or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            errors = await asyncio.gather(*(self._tell(client, notice) for notice in due))
        finally:
            if self._http is None:
                await client.aclose()
        for notice, error in zip(due, errors):
            if error is None:
                await self._notices.confirmed(notice.org_id, notice.client_id)
                delivered.confirmed.append(DeliveredNotice.of(notice))
                continue
            retry_at = now + self.retry_delay(notice.attempts)
            await self._notices.unconfirmed(notice.org_id, notice.client_id, retry_at=retry_at, error=error)
            delivered.unconfirmed.append(DeliveredNotice.of(notice))
            logger.error(
                'Organisation deletion not confirmed by %s (%s, attempt %d); trying again at %s',
                notice.service_name,
                error,
                notice.attempts + 1,
                retry_at.isoformat(),
            )
        return delivered

    async def _tell(self, client: httpx.AsyncClient, notice: DueNotice) -> str | None:
        """None when the service confirmed; what went wrong otherwise."""
        token = self._tokens.sign(
            subject=self._tokens.issuer,
            audience=notice.base_url,
            use=TokenUse.IGNI,
            lifetime=CALL_TOKEN_LIFETIME,
            claims=CustomClaims(org=notice.org_id),
        )
        try:
            response = await client.delete(
                f'{notice.base_url}/v1/igni/organisations/{notice.org_id}',
                headers={'Authorization': f'Bearer {token}'},
            )
        except httpx.HTTPError as exc:
            return type(exc).__name__
        if response.is_success:
            return None
        return f'HTTP {response.status_code}'

    # ── The loop ────────────────────────────────────────────────────

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(self._wake.arm()), name='organisation-deletions')

    async def stop(self) -> None:
        """Cancel the loop on shutdown, and wait for it to unwind."""
        self._wake.disarm()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — shutdown is not a place to raise
            logger.exception('Organisation deletion loop did not unwind cleanly')

    async def _loop(self, wake: asyncio.Event) -> None:
        while True:
            try:
                await self.deliver_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never let one bad round end the loop: the notices are
                # still there, and the next round delivers them.
                logger.exception('Organisation deletion round failed; will try again')
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=INTERVAL_SECONDS)
            wake.clear()
