"""The installed licence file, read when it changes.

Every component reads the same file: igni server, each paid module and
the Fleet gateway. In Kubernetes it is one Secret mounted into each of
them (`licence.existingSecret` in the chart); in compose, one file
bind-mounted into each. Renewing is replacing that file. Mounted
Secrets are updated in place, so nothing needs restarting: the file is
looked at again at most every `RECHECK` and re-verified when it changed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import threading
from typing import Protocol

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


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class LicenceFile:
    """The licence at one path: verified, cached, and read again when it changes."""

    def __init__(self, path: Path, *, verifier: LicenceVerifier, clock: Clock | None = None) -> None:
        self._path = path
        self._verifier = verifier
        self._clock = clock or SystemClock()
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

    def reading(self) -> LicenceReading:
        """The verified payload, or why there is none. Cheap: a `stat` at most once a `RECHECK`."""
        now = self._clock.now()
        with self._lock:
            if self._checked_at is None or now - self._checked_at >= RECHECK or now < self._checked_at:
                self._checked_at = now
                self._refresh()
            return self._reading

    def status(self, module: Module) -> ModuleStatus:
        return LicenceStatus.of(self.reading(), module, now=self._clock.now())

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
