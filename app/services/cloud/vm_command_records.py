"""Commands and jobs as stored, as their API records."""

from __future__ import annotations

from app.api.base_v1.schema.vm_commands import HandedOutCommand, JobScope, Requester, VmCommandRecord, VmJobRecord
from app.db.sql.models.vm_command import VmCommand, VmJob
from app.security.producer import ProducerRef


class VmCommandRecords:
    """Maps `vm_commands` and `vm_jobs` rows onto what the API answers with."""

    @staticmethod
    def requester(row: VmCommand | VmJob) -> Requester:
        return Requester(kind=row.requested_by_kind, id=row.requested_by_id, acting_as=row.requested_as)

    @classmethod
    def command(cls, org_id: str, row: VmCommand) -> VmCommandRecord:
        return VmCommandRecord(
            command_id=row.id,
            org_id=org_id,
            producer=ProducerRef.stored(row.producer_kind, row.producer_id),
            command=row.command,
            timeout_s=row.timeout_s,
            job_id=row.job_id,
            wake=row.wake,
            requested_by=cls.requester(row),
            state=row.state,
            handed_to=row.handed_to,
            created_at=row.created_at,
            handed_out_at=row.handed_out_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            exit_code=row.exit_code,
            output=row.output,
            error=row.error,
        )

    @classmethod
    def handed_out(cls, row: VmCommand) -> HandedOutCommand:
        return HandedOutCommand(
            command_id=row.id,
            command=row.command,
            timeout_s=row.timeout_s,
            job_id=row.job_id,
            requested_by=cls.requester(row),
            created_at=row.created_at,
        )

    @classmethod
    def job(cls, org_id: str, job: VmJob, handed: int = 0, finished: int = 0) -> VmJobRecord:
        return VmJobRecord(
            job_id=job.id,
            org_id=org_id,
            scope=JobScope.stored(job.scope_kind, job.scope_id),
            command=job.command,
            timeout_s=job.timeout_s,
            requested_by=cls.requester(job),
            created_at=job.created_at,
            cancelled_at=job.cancelled_at,
            handed_out=handed,
            finished=finished,
        )
