"""Pydantic schemas for commands in producers' VMs (`docs/service-identity.md` §13)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.security.producer import ProducerRef

#: The longest command, in characters. It is a shell line, not a script
#: upload: anything longer belongs in a profile's setup script.
MAX_COMMAND = 16384

#: The most of a command's output kept, from its end.
MAX_OUTPUT = 65536

#: The default and longest time a command may run.
DEFAULT_TIMEOUT_S = 600
MAX_TIMEOUT_S = 3600


class RunCommandRequest(BaseModel):
    """Run one command, as root, in one producer's VM — waking it."""

    producer: ProducerRef
    #: The organisation of the producer; the caller's own when left out.
    #: Only a super admin names another.
    org_id: str | None = None
    command: str = Field(min_length=1, max_length=MAX_COMMAND)
    timeout_s: int = Field(default=DEFAULT_TIMEOUT_S, ge=1, le=MAX_TIMEOUT_S)


class JobScope(BaseModel):
    """Which VMs a job is for."""

    kind: Literal['group', 'profile', 'org', 'platform']
    #: The group's or profile's id; nothing for `org` or `platform`.
    id: str | None = None


class QueueJobRequest(BaseModel):
    """One command for many VMs, each run at that VM's next wake. Wakes nobody."""

    scope: JobScope
    org_id: str | None = None
    command: str = Field(min_length=1, max_length=MAX_COMMAND)
    timeout_s: int = Field(default=DEFAULT_TIMEOUT_S, ge=1, le=MAX_TIMEOUT_S)


class Requester(BaseModel):
    kind: str
    id: str
    #: `producer`, `org_admin` or `super_admin`.
    acting_as: str


class VmCommandRecord(BaseModel):
    """One command in one VM — and its audit record."""

    command_id: str
    org_id: str
    producer: ProducerRef
    command: str
    timeout_s: int
    job_id: str | None
    wake: bool
    requested_by: Requester
    state: str
    handed_to: str | None
    created_at: datetime
    handed_out_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    output: str | None
    error: str | None


class VmCommandList(BaseModel):
    commands: list[VmCommandRecord]


class VmJobRecord(BaseModel):
    job_id: str
    org_id: str
    scope: JobScope
    command: str
    timeout_s: int
    requested_by: Requester
    created_at: datetime
    cancelled_at: datetime | None
    #: How many VMs have been handed it, and how many finished it.
    handed_out: int = 0
    finished: int = 0


class VmJobList(BaseModel):
    jobs: list[VmJobRecord]


class QueuedJobs(BaseModel):
    """A job as queued: one per organisation it reaches — several for a platform job, under one id."""

    jobs: list[VmJobRecord]


# ── The node agent's side ─────────────────────────────────────────────


class HandedOutCommand(BaseModel):
    """A command the node agent is to run in the VM, as root, with `/bin/sh -c`."""

    command_id: str
    command: str
    timeout_s: int
    job_id: str | None
    requested_by: Requester
    created_at: datetime


class PulledCommands(BaseModel):
    commands: list[HandedOutCommand]


class CommandResult(BaseModel):
    """What the node agent reports when a command has finished, or could not run."""

    #: Null when it did not run at all (`error` says why) or was killed
    #: at its timeout.
    exit_code: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #: The end of stdout and stderr together; longer is cut from the front.
    output: str | None = None
    error: str | None = Field(default=None, max_length=2000)


class WakeReason(BaseModel):
    org_id: str
    producer: ProducerRef
    #: `command` (a command for this one VM is waiting) and/or `event`.
    reasons: list[str]


class Wakes(BaseModel):
    """Producers whose VMs should be woken now."""

    producers: list[WakeReason]
