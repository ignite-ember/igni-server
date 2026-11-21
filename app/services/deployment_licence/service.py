"""The deployment's licence, as igni server shows it and installs it.

igni server does not enforce the licence. It is Apache-2.0, and a check
here would be two lines to delete; every paid module verifies the same
file itself, with the public key built into it, and decides for itself
what it may do (docs/licence-file.md). What igni server does:

* shows a super admin where each module stands (`overview`);
* tells everybody who signs in to the portal when something needs
  doing — a licence expiring, lapsed or read-only (`notice`);
* says so while the clock is behind a time the deployment has already
  seen, at which the licence is then evaluated (`clock_warning`; the
  mark is kept by `LicenceClockKeeper`, as in every module);
* installs a licence a super admin uploads, after verifying it, when the
  licence path is writable (`install`). Where it is a mounted Secret,
  the Secret is updated instead and this says so.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile

from fastapi import Request
from starlette.applications import Starlette

from app.config import settings
from app.services.deployment_licence.integrity_state import EncryptedIntegrityStore, IntegrityState
from app.services.deployment_licence.schemas import IntegrityNotice, LicenceNotice, LicenceOverview, LicenceWarning
from app.services.deployment_licence.sealed_report import (
    build_report,
    INTEGRITY_REPORT_PUBLIC_KEY,
    seal,
    SealedReport,
)
from app.services.licence import (
    HighWaterMark,
    LicenceEnvelope,
    LicenceError,
    LicenceFile,
    LicenceState,
    LicenceVerifier,
    Module,
    ModuleStatus,
    Reason,
    TrustedKeys,
)

#: The neutral sentence a super admin sees when the integrity file has been tampered with. It says what,
#: not how, so it cannot help whoever did it; the detail is in the audit trail and the sealed report.
_TAMPER_NOTICE = (
    "This server's licence-integrity data has been altered or replaced. "
    'The licence is being evaluated from the audit trail. Contact Ignite Ember.'
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

    def __init__(
        self,
        source: LicenceFile,
        *,
        verifier: LicenceVerifier,
        store: EncryptedIntegrityStore | None = None,
    ) -> None:
        """`verifier` must be the one `source` verifies with: an upload is checked as the installed file is."""
        self._source = source
        self._verifier = verifier
        self._store = store or EncryptedIntegrityStore(Path(settings.igni_state_dir))

    @classmethod
    def from_settings(cls) -> DeploymentLicence:
        """The licence at the configured path, verified against this environment's keys."""
        verifier = LicenceVerifier(TrustedKeys.for_environment(settings.app_env))
        source = LicenceFile(Path(settings.igni_licence_file), verifier=verifier, high_water=HighWaterMark())
        return cls(source, verifier=verifier, store=EncryptedIntegrityStore(Path(settings.igni_state_dir)))

    @classmethod
    def of_app(cls, app: Starlette) -> DeploymentLicence:
        """The app's instance, built on first use, so its cached reading is shared by every request."""
        licence: DeploymentLicence | None = getattr(app.state, 'deployment_licence', None)
        if licence is None:
            licence = app.state.deployment_licence = cls.from_settings()
        return licence

    @property
    def high_water(self) -> HighWaterMark:
        return self._source.high_water

    @property
    def store(self) -> EncryptedIntegrityStore:
        return self._store

    def _integrity_notice(self) -> IntegrityNotice | None:
        """A neutral admin-only note when the integrity file has been tampered with; None while it is sound."""
        state = self._store.integrity()
        tampered = state.tampered or bool(state.recent_tamper())
        if not tampered:
            return None
        return IntegrityNotice(tampered=True, message=_TAMPER_NOTICE, events=len(state.recent_tamper()))

    def statuses(self) -> list[ModuleStatus]:
        return [self._source.status(module) for module in Module]

    def status(self, module: Module) -> ModuleStatus:
        return self._source.status(module)

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
            clock=self._source.clock_warning(),
            integrity=self._integrity_notice(),
        )

    def sealed_report(self) -> SealedReport:
        """Gather and vendor-seal the deployment's licence-integrity report, for a super admin to hand over."""
        reading = self._source.reading()
        state: IntegrityState = self._store.integrity()
        report = build_report(
            licence_id=reading.payload.licence_id if reading.payload else None,
            customer=reading.payload.customer if reading.payload else None,
            mark=self._source.high_water.seen,
            clock_warning=self._source.clock_warning(),
            state=state,
            modules=self.statuses(),
        )
        pem = settings.integrity_report_public_key_pem or INTEGRITY_REPORT_PUBLIC_KEY
        return seal(report, public_key_pem=pem, key_hint=settings.integrity_report_kms_key or None)

    def notice(self) -> LicenceNotice:
        """What the portal shows everybody: one line per module that needs attention, and the licensee.

        The clock warning and the tamper notice are admin-only; they are on
        the super admin's `overview`, not here.
        """
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
        # Verified here, not through the installed file's reader: that one logs a refusal against the
        # installed path, which named the file on disk "not accepted" when only the upload was.
        try:
            payload = self._verifier.verify(raw)
        except LicenceError as exc:
            reason = Reason(exc.reason)
            logger.warning(
                'licence: an uploaded licence was refused (%s): %s; the installed licence at %s is unchanged',
                reason.value,
                exc,
                self._source.path,
            )
            raise LicenceRejected(reason, f'This file is not a licence igni accepts ({reason.value}).') from exc
        self._write(raw)
        self._source.forget()
        logger.warning('licence: installed %s for %s at %s', payload.licence_id, payload.customer, self._source.path)
        return self.overview()

    def reject_malformed(self) -> LicenceRejected:
        """An upload that is not even shaped like a licence file: refused as `malformed`, like any other."""
        logger.warning(
            'licence: an uploaded licence was refused (malformed): not a licence file; '
            'the installed licence at %s is unchanged',
            self._source.path,
        )
        return LicenceRejected(Reason.MALFORMED, f'This file is not a licence igni accepts ({Reason.MALFORMED.value}).')

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


def deployment_licence(request: Request) -> DeploymentLicence:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return DeploymentLicence.of_app(request.app)
