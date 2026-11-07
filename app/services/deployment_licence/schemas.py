"""What the portal is told about the licence."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.services.licence import LicenceKind, LicenceState, Module, ModuleStatus, Reason


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
    beyond its contract says whose it is.
    """

    warnings: list[LicenceWarning]
    customer: str | None = None
    licence_id: str | None = None
