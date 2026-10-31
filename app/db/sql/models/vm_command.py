"""VmCommand and VmJob models — commands run as root in producers' microVMs.

A **command** is one run in one producer's VM, and its row is the audit
record: who asked, as what, which producer's VM, the command, when it was
handed to the node agent, started and finished, and the exit code. A
command for one VM wakes it (`wake`).

A **job** is one command for many VMs — a group's, a profile's, the
organisation's, or every organisation's (a super admin's, fanned out
under one id). It wakes nobody: each VM in its scope gets it at its next
wake, as a command row of its own, until the job is cancelled
(`docs/service-identity.md` §13).
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, func, Index, Integer, Text, text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase

QUEUED = 'queued'
HANDED_OUT = 'handed_out'
RUNNING = 'running'
FINISHED = 'finished'
CANCELLED = 'cancelled'


class VmJob(TenantBase):
    __tablename__ = 'vm_jobs'
    __table_args__ = (
        CheckConstraint("scope_kind IN ('group', 'profile', 'org', 'platform')", name='ck_vm_jobs_scope_kind'),
        Index('idx_vm_jobs_active', 'cancelled_at'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    #: `group` / `profile` (its id in `scope_id`), `org`, or `platform`
    #: (a super admin's job for every organisation, the same id in each).
    scope_kind: Mapped[str] = Column(Text, nullable=False)
    scope_id: Mapped[str | None] = Column(Text, nullable=True)
    command: Mapped[str] = Column(Text, nullable=False)
    timeout_s: Mapped[int] = Column(Integer, nullable=False)
    requested_by_kind: Mapped[str] = Column(Text, nullable=False)
    requested_by_id: Mapped[str] = Column(Text, nullable=False)
    #: `org_admin` or `super_admin`.
    requested_as: Mapped[str] = Column(Text, nullable=False)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )
    cancelled_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    cancelled_by_id: Mapped[str | None] = Column(Text, nullable=True)


class VmCommand(TenantBase):
    __tablename__ = 'vm_commands'
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued', 'handed_out', 'running', 'finished', 'cancelled')", name='ck_vm_commands_state'
        ),
        Index('idx_vm_commands_producer', 'producer_kind', 'producer_id', 'state'),
        Index('idx_vm_commands_job', 'job_id', 'producer_kind', 'producer_id', unique=True),
        Index('idx_vm_commands_created', 'created_at'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    producer_kind: Mapped[str] = Column(Text, nullable=False)
    producer_id: Mapped[str] = Column(Text, nullable=False)
    command: Mapped[str] = Column(Text, nullable=False)
    timeout_s: Mapped[int] = Column(Integer, nullable=False)
    #: The job this run is part of; null for a command for one VM.
    job_id: Mapped[str | None] = Column(Text, ForeignKey('vm_jobs.id', ondelete='SET NULL'), nullable=True)
    #: A command for one VM wakes it; a job's waits for the next wake.
    wake: Mapped[bool] = Column(Boolean, nullable=False, server_default=text('false'), default=False)
    requested_by_kind: Mapped[str] = Column(Text, nullable=False)
    requested_by_id: Mapped[str] = Column(Text, nullable=False)
    #: `producer` (in its own VM), `org_admin` or `super_admin`.
    requested_as: Mapped[str] = Column(Text, nullable=False)
    state: Mapped[str] = Column(Text, nullable=False, default=QUEUED)
    #: The platform client (node agent) it was handed to.
    handed_to: Mapped[str | None] = Column(Text, nullable=True)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )
    handed_out_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = Column(Integer, nullable=True)
    #: The end of its output, as the node agent kept it.
    output: Mapped[str | None] = Column(Text, nullable=True)
    #: Why it did not run, when it did not.
    error: Mapped[str | None] = Column(Text, nullable=True)
