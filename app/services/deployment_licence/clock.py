"""The licence's clock in igni server: the deployment's single high-water mark.

igni server holds the one mark for the whole deployment — the latest time
it has seen — in the encrypted integrity file (`EncryptedIntegrityStore`),
on a writable, persisted volume. The paid modules and the Fleet gateway
read this mark from igni server rather than keeping their own; igni server
enforces nothing, but its banner must agree with the modules that do.

The mark is seeded, on the first start of this release, from the newest
`licence:daily` audit event, and only moves on while this process's clock
and the database's agree within 48 hours (`app.services.licence.clock`).
"""

from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.db.sql.advisory_locks import AdvisoryLockDB
from app.services.deployment_licence.integrity_state import EncryptedIntegrityStore
from app.services.licence import Clock, HighWaterMark, LicenceClock


class LicenceClockKeeper(LicenceClock):
    """`LicenceClock` backed by igni server's encrypted integrity file."""

    def __init__(
        self,
        mark: HighWaterMark,
        *,
        store: EncryptedIntegrityStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._integrity = store or EncryptedIntegrityStore(Path(settings.igni_state_dir), locks=AdvisoryLockDB())
        super().__init__(mark, self._integrity, clock=clock)

    @property
    def store(self) -> EncryptedIntegrityStore:
        return self._integrity
