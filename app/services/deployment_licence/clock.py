"""The licence's clock in igni server: the latest time seen, kept on the shared plane.

igni server enforces nothing, but its banner must agree with the modules
that do, so it keeps the same high-water mark they keep in their own
databases (`app.services.licence.clock`). Seeded, on the first start of
this release, from the newest `licence:daily` audit event.
"""

from __future__ import annotations

from app.db.sql.licence_clock import LicenceClockDB
from app.services.licence import Clock, ClockStore, HighWaterMark, LicenceClock


class LicenceClockKeeper(LicenceClock):
    """`LicenceClock` on igni server's own database."""

    def __init__(self, mark: HighWaterMark, *, store: ClockStore | None = None, clock: Clock | None = None) -> None:
        super().__init__(mark, store or LicenceClockDB(), clock=clock)
