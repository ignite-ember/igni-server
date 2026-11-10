"""Discarding an app: removed at once, its VM erased in the background, optionally a fresh app in its place."""

from __future__ import annotations

from app.api.base_v1.schema.portal.apps import DiscardedApp
from app.db.sql.models.registered_client import APP
from app.services.cloud.erasures import Erasures
from app.services.cloud.producer_feed import ProducerFeed
from app.services.portal.app_fresh_start import AppFreshStart
from app.services.portal.apps import OrgApps


class AppDiscard:
    """Removes an app, tells the feed, and reports where its erasure stands."""

    def __init__(
        self,
        *,
        apps: OrgApps | None = None,
        feed: ProducerFeed | None = None,
        erasures: Erasures | None = None,
        fresh_start: AppFreshStart | None = None,
    ) -> None:
        self._apps = apps or OrgApps()
        self._feed = feed or ProducerFeed()
        self._erasures = erasures or Erasures()
        self._fresh_start = fresh_start or AppFreshStart(apps=self._apps)

    async def discard(self, org_id: str, app_id: str, *, by_user_id: str, start_fresh: bool = False) -> DiscardedApp:
        """The app removed at once, its VM erased in the background; optionally a fresh app in its place.

        Removed now: its tokens and credential stop, its spaces go with it,
        its VM can never be woken again, and the feed lists it removed at
        once, so the gateway frees its place under the licence and erases
        its VM straight away — unless a legal hold stands on it
        (`docs/service-identity.md` §18.5).
        """
        client = await self._apps.client(org_id, app_id)
        carried = await self._fresh_start.carried_over(org_id, client) if start_fresh else None
        await self._apps.remove(org_id, app_id)
        await self._feed.discarded_app(org_id, app_id, name=client.name, by_user_id=by_user_id)
        erasures = await self._erasures.of(org_id, kind=APP, producer_id=app_id)
        fresh = (
            await self._fresh_start.start(org_id, carried=carried, by_user_id=by_user_id)
            if carried is not None
            else None
        )
        return DiscardedApp(discarded=erasures[0] if erasures else None, fresh_app=fresh)
