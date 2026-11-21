"""The installed licence file, read when it changes.

Every component reads the same file: igni server, each paid module and
the Fleet gateway. In Kubernetes it is one Secret mounted into each of
them (`licence.existingSecret` in the chart); in compose, one file
bind-mounted into each. Renewing is replacing that file. Mounted
Secrets are updated in place, so nothing needs restarting: the file is
looked at again at most every `RECHECK` and re-verified when it changed.

The licence is evaluated at `evaluated_at`: now, unless the clock is
behind a time the deployment has already seen (`clock.HighWaterMark`),
and never before the licence was issued.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from pathlib import Path
import threading

from .clock import Clock, HighWaterMark, SystemClock
from .format import Module
from .status import LicenceReading, LicenceStatus, ModuleStatus, Reason
from .verifier import LicenceError, LicenceVerifier

logger = logging.getLogger(__name__)

#: Where the licence is installed unless configured otherwise (`IGNI_LICENCE_FILE`).
DEFAULT_LICENCE_PATH = Path('/etc/igni/licence/licence.json')
#: How often the file is looked at again.
RECHECK = timedelta(seconds=60)
#: The largest file that is read. A licence is well under 2 KiB.
MAX_BYTES = 64 * 1024


class LicenceFile:
    """The licence at one path: verified, cached, and read again when it changes."""

    def __init__(
        self,
        path: Path,
        *,
        verifier: LicenceVerifier,
        clock: Clock | None = None,
        high_water: HighWaterMark | None = None,
    ) -> None:
        self._path = path
        self._verifier = verifier
        self._clock = clock or SystemClock()
        self._high_water = high_water or HighWaterMark()
        self._lock = threading.Lock()
        self._seen: tuple[int, int] | None = None
        self._checked_at: datetime | None = None
        self._reading = LicenceReading(problem=Reason.NO_LICENCE)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def high_water(self) -> HighWaterMark:
        return self._high_water

    def reading(self) -> LicenceReading:
        """The verified payload, or why there is none. Cheap: a `stat` at most once a `RECHECK`."""
        now = self._clock.now()
        with self._lock:
            if self._checked_at is None or now - self._checked_at >= RECHECK or now < self._checked_at:
                self._checked_at = now
                self._refresh()
            return self._reading

    def status(self, module: Module) -> ModuleStatus:
        reading = self.reading()
        return LicenceStatus.of(reading, module, now=self.evaluated_at(reading))

    def evaluated_at(self, reading: LicenceReading | None = None) -> datetime:
        """The time the licence is evaluated at: now, but never more than the tolerance before a time already
        seen, and never before the licence was issued (a licence issued at T proves it is at least T)."""
        if reading is None:
            reading = self.reading()
        at = self._high_water.evaluate_at(self._clock.now())
        if reading.payload is not None:
            at = max(at, reading.payload.issued)
        return at

    def clock_warning(self) -> str | None:
        """The sentence for the admin while the clock is behind a time already seen. None while it is not, and
        without a licence, whose evaluation time then changes nothing."""
        seen = self._high_water.behind(self._clock.now())
        if seen is None or self.reading().payload is None:
            return None
        return HighWaterMark.warning(seen)

    def forget(self) -> None:
        """Read the file again at the next question, whatever its timestamp says (after an install)."""
        with self._lock:
            self._seen = None
            self._checked_at = None

    def _refresh(self) -> None:
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            self._seen, self._reading = None, LicenceReading(problem=Reason.NO_LICENCE)
            return
        except OSError as exc:
            logger.warning('licence: cannot read %s: %s', self._path, exc)
            self._seen, self._reading = None, LicenceReading(problem=Reason.NO_LICENCE)
            return
        seen = (stat.st_mtime_ns, stat.st_size)
        if seen == self._seen:
            return
        self._seen = seen
        self._reading = self.read_bytes(self._load())

    def read_bytes(self, raw: bytes | None) -> LicenceReading:
        """Verify `raw` as a licence file. Used for the installed file and for an upload before installing it."""
        if raw is None:
            return LicenceReading(problem=Reason.NO_LICENCE)
        try:
            payload = self._verifier.verify(raw)
        except LicenceError as exc:
            logger.warning('licence: %s is not accepted (%s): %s', self._path, exc.reason, exc)
            return LicenceReading(problem=Reason(exc.reason))
        logger.info('licence: %s for %s, modules %s', payload.licence_id, payload.customer, sorted(payload.modules))
        return LicenceReading(payload=payload)

    def _load(self) -> bytes | None:
        try:
            with self._path.open('rb') as handle:
                return handle.read(MAX_BYTES)
        except OSError as exc:
            logger.warning('licence: cannot read %s: %s', self._path, exc)
            return None
