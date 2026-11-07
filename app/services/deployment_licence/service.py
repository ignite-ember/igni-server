"""The deployment's licence, as igni server shows it and installs it.

igni server does not enforce the licence. It is Apache-2.0, and a check
here would be two lines to delete; every paid module verifies the same
file itself, with the public key built into it, and decides for itself
what it may do (docs/licence-file.md). What igni server does:

* shows a super admin where each module stands (`overview`);
* tells everybody who signs in to the portal when something needs
  doing — a licence expiring, lapsed or read-only (`notice`);
* installs a licence a super admin uploads, after verifying it, when the
  licence path is writable (`install`). Where it is a mounted Secret,
  the Secret is updated instead and this says so.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile
from typing import ClassVar

from app.config import settings
from app.services.deployment_licence.schemas import LicenceNotice, LicenceOverview, LicenceWarning
from app.services.licence import (
    LicenceEnvelope,
    LicenceFile,
    LicenceState,
    LicenceVerifier,
    Module,
    ModuleStatus,
    Reason,
    TrustedKeys,
)

logger = logging.getLogger(__name__)

#: Problems that mean "a file is installed but it is no good" — worth a banner even on a deployment
#: that has no module licensed, because somebody installed it meaning to.
_BROKEN = frozenset({Reason.MALFORMED, Reason.UNKNOWN_KEY, Reason.INVALID_SIGNATURE})


class LicenceRejected(Exception):
    """The upload is not a licence this release accepts. `reason` says why."""

    def __init__(self, reason: Reason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class LicencePathReadOnly(Exception):
    """The licence path cannot be written here: it is a mounted Secret, or a read-only volume."""


class DeploymentLicence:
    """The licence file igni server shares with the paid modules."""

    _current: ClassVar[DeploymentLicence | None] = None

    def __init__(self, source: LicenceFile) -> None:
        self._source = source

    @classmethod
    def current(cls) -> DeploymentLicence:
        """The process's one, built from settings on first use."""
        if cls._current is None:
            verifier = LicenceVerifier(TrustedKeys.for_environment(settings.app_env))
            cls._current = cls(LicenceFile(Path(settings.igni_licence_file), verifier=verifier))
        return cls._current

    @classmethod
    def use(cls, licence: DeploymentLicence | None) -> None:
        """Run on `licence` (a test's, with its own key and clock); None goes back to settings."""
        cls._current = licence

    def statuses(self) -> list[ModuleStatus]:
        return [self._source.status(module) for module in Module]

    def overview(self) -> LicenceOverview:
        reading = self._source.reading()
        return LicenceOverview(
            path=str(self._source.path),
            installed=reading.payload is not None or reading.problem in _BROKEN,
            problem=reading.problem,
            licence_id=reading.payload.licence_id if reading.payload else None,
            customer=reading.payload.customer if reading.payload else None,
            kind=reading.payload.kind if reading.payload else None,
            issued=reading.payload.issued if reading.payload else None,
            modules=self.statuses(),
        )

    def notice(self) -> LicenceNotice:
        """What the portal shows everybody: one line per module that needs attention, and the licensee."""
        warnings = [
            LicenceWarning(module=status.module, state=status.state, message=status.message)
            for status in self.statuses()
            if status.message is not None and self._worth_a_banner(status)
        ]
        # Only a verified licence names anybody: a file that fails its signature could say anything.
        payload = self._source.reading().payload
        return LicenceNotice(
            warnings=warnings,
            customer=payload.customer if payload else None,
            licence_id=payload.licence_id if payload else None,
        )

    def install(self, envelope: LicenceEnvelope) -> LicenceOverview:
        """Verify an uploaded licence and, if it is good, put it where every component reads it."""
        raw = envelope.model_dump_json(indent=2).encode('utf-8') + b'\n'
        reading = self._source.read_bytes(raw)
        if reading.payload is None:
            reason = reading.problem or Reason.MALFORMED
            raise LicenceRejected(reason, f'This file is not a licence igni accepts ({reason.value}).')
        self._write(raw)
        self._source.forget()
        logger.warning(
            'licence: installed %s for %s at %s',
            reading.payload.licence_id,
            reading.payload.customer,
            self._source.path,
        )
        return self.overview()

    @staticmethod
    def _worth_a_banner(status: ModuleStatus) -> bool:
        if status.state is not LicenceState.UNLICENSED:
            return True
        return status.reason in _BROKEN or status.reason in {Reason.EVALUATION_EXPIRED, Reason.REQUIRES_FACTORY}

    def _write(self, raw: bytes) -> None:
        """Atomically: a reader sees the old file or the new one, never half of one."""
        path = self._source.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix='.licence-', dir=path.parent)
        except OSError as exc:
            raise LicencePathReadOnly(str(path)) from exc
        try:
            with os.fdopen(handle, 'wb') as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        except OSError as exc:
            Path(temporary).unlink(missing_ok=True)
            raise LicencePathReadOnly(str(path)) from exc
