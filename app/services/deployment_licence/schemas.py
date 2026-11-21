"""What the portal is told about the licence."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.services.licence import LicenceKind, LicenceState, Module, ModuleStatus, Reason


class IntegrityNotice(BaseModel):
    """A neutral, admin-only note that the deployment's licence-integrity data has been tampered with.

    Deliberately says nothing an operator could use to cover their tracks:
    that something is wrong, and to contact the vendor, nothing more. The
    detail is in the audit trail and the sealed report, not here.
    """

    tampered: bool
    #: The sentence a super admin sees. None while nothing is wrong.
    message: str | None = None
    #: How many tamper events the integrity file has kept. For the super admin's own record.
    events: int = 0


class LicenceOverview(BaseModel):
    """Everything a super admin sees: the installed file and each module's standing."""

    path: str
    #: A file is installed, good or not.
    installed: bool
    #: Why there is no usable licence, when there is none.
    problem: Reason | None = None
    licence_id: str | None = None
    customer: str | None = None
    kind: LicenceKind | None = None
    issued: datetime | None = None
    modules: list[ModuleStatus]
    #: Admin-only. Set while the clock is behind a time this deployment has already seen; the licence is
    #: then evaluated as of no earlier than that time minus a day.
    clock: str | None = None
    #: Admin-only. Set when the integrity file has been tampered with.
    integrity: IntegrityNotice | None = None


class LicenceWarning(BaseModel):
    """One module that needs attention, in a sentence anybody can act on."""

    module: Module
    state: LicenceState
    message: str


class LicenceNotice(BaseModel):
    """What everybody signed in sees: the banner, and whose licence this is.

    `warnings` is empty when nothing needs doing. `customer` and
    `licence_id` name the licensee of a verified licence, and are None
    when there is none — no file, or one that is not good. The portal
    shows them quietly on every page, so a deployment copied or shared
    beyond its contract says whose it is. The clock warning and the
    integrity notice are admin-only and live on `LicenceOverview`, not
    here: everybody sees that a licence is expiring; only a super admin is
    told the clock is wrong or the integrity file was touched.
    """

    warnings: list[LicenceWarning]
    customer: str | None = None
    licence_id: str | None = None
