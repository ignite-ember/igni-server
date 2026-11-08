"""Commands as root in producers' microVMs, and jobs for many of them.

Who may run what is `VmCommandAuthority`'s (`docs/service-identity.md`
§13, igni's `docs/CLOUD.md` §7).

A command for one VM wakes it. A job — one command for a group's, a
profile's, an organisation's or (the super admin's) every
organisation's VMs — wakes nobody: each VM in its scope is handed it at
its next wake. igni server stores, hands out and records; the node
agent runs the command through the guest agent and posts the result.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi import HTTPException, status

from app.api.base_v1.schema.vm_commands import (
    CommandResult,
    MAX_OUTPUT,
    PulledCommands,
    QueuedJobs,
    QueueJobRequest,
    RunCommandRequest,
    VmCommandList,
    VmCommandRecord,
    VmJobList,
    VmJobRecord,
    WakeReason,
)
from app.db.sql.models.vm_command import VmCommand, VmJob
from app.db.sql.organizations import OrganizationDB
from app.db.sql.schemas.vm_command import CommandOutcome
from app.db.sql.tenant_registry import TenantRegistryDB
from app.db.sql.vm_commands import VmCommandsDB
from app.security.producer import ProducerCaller, ProducerRef
from app.services.cloud.producer_lookup import ProducerLookup
from app.services.cloud.vm_command_authority import Acting, VmCommandAuthority
from app.services.cloud.vm_command_records import VmCommandRecords
from app.services.cloud.vm_job_scopes import JobScopes


class VmCommands:
    """What people and apps ask for; what the node agent pulls and reports."""

    def __init__(
        self,
        *,
        db: VmCommandsDB | None = None,
        authority: VmCommandAuthority | None = None,
        producers: ProducerLookup | None = None,
        scopes: JobScopes | None = None,
        organisations: OrganizationDB | None = None,
        tenants: TenantRegistryDB | None = None,
    ) -> None:
        self._db = db or VmCommandsDB()
        self._authority = authority or VmCommandAuthority()
        self._producers = producers or ProducerLookup()
        self._scopes = scopes or JobScopes()
        self._organisations = organisations or OrganizationDB()
        self._tenants = tenants or TenantRegistryDB()

    # ── Asking ──────────────────────────────────────────────────────

    async def run(self, caller: ProducerCaller, body: RunCommandRequest) -> VmCommandRecord:
        acting = self._authority.over_one(caller, body.org_id, body.producer)
        await self._producers.exists(acting.org_id, body.producer)
        row = await self._db.add(
            acting.org_id,
            VmCommand(
                producer_kind=body.producer.kind,
                producer_id=body.producer.id,
                command=body.command,
                timeout_s=body.timeout_s,
                wake=True,
                requested_by_kind=acting.requester.kind,
                requested_by_id=acting.requester.id,
                requested_as=acting.requester.acting_as,
            ),
        )
        return VmCommandRecords.command(acting.org_id, row)

    async def _reached(self, caller: ProducerCaller, body: QueueJobRequest) -> tuple[Acting, list[str]]:
        """Who queues the job, and every organisation it is queued in."""
        if body.scope.kind == 'platform':
            acting = self._authority.over_platform(caller)
            # Every organisation there is now, provisioning a database
            # for one that has none yet: one created later is not reached.
            return acting, [str(org.id) for org, _ in await self._organisations.all_with_member_counts()]
        acting = self._authority.over_many(caller, body.org_id)
        if await self._organisations.get_by_id(acting.org_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Organisation not found')
        await self._scopes.check(acting.org_id, body.scope)
        return acting, [acting.org_id]

    async def queue(self, caller: ProducerCaller, body: QueueJobRequest) -> QueuedJobs:
        acting, orgs = await self._reached(caller, body)
        job_id = str(uuid.uuid4())
        queued = []
        for org_id in orgs:
            job = await self._db.add_job(
                org_id,
                VmJob(
                    id=job_id,
                    scope_kind=body.scope.kind,
                    scope_id=body.scope.id,
                    command=body.command,
                    timeout_s=body.timeout_s,
                    requested_by_kind=acting.requester.kind,
                    requested_by_id=acting.requester.id,
                    requested_as=acting.requester.acting_as,
                ),
            )
            queued.append(VmCommandRecords.job(org_id, job))
        return QueuedJobs(jobs=queued)

    async def cancel(self, caller: ProducerCaller, org_id: str | None, command_id: str) -> None:
        org = org_id or caller.org_id or ''
        row = await self._db.get(org, command_id) if org else None
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Command not found')
        self._authority.over_one(caller, org, ProducerRef.stored(row.producer_kind, row.producer_id))
        if not await self._db.cancel(org, command_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Already handed to the VM')

    async def cancel_job(self, caller: ProducerCaller, org_id: str | None, job_id: str) -> VmJobRecord:
        acting = self._authority.over_many(caller, org_id)
        job = await self._db.cancel_job(acting.org_id, job_id, caller.id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Job not found')
        return VmCommandRecords.job(acting.org_id, job)

    # ── The audit log ───────────────────────────────────────────────

    async def log(
        self,
        caller: ProducerCaller,
        *,
        org_id: str | None,
        producer: ProducerRef | None,
        job_id: str | None,
        limit: int,
    ) -> VmCommandList:
        """Org admins and the super admin read an organisation's; a producer reads its own."""
        reading = self._authority.over_log(caller, org_id)
        producer = reading.only or producer
        rows = await self._db.recent(
            reading.org_id,
            producer_kind=producer.kind if producer else None,
            producer_id=producer.id if producer else None,
            job_id=job_id,
            limit=limit,
        )
        return VmCommandList(commands=[VmCommandRecords.command(reading.org_id, row) for row in rows])

    async def jobs(self, caller: ProducerCaller, org_id: str | None, limit: int) -> VmJobList:
        acting = self._authority.over_many(caller, org_id)
        return VmJobList(
            jobs=[
                VmCommandRecords.job(acting.org_id, job, handed, finished)
                for job, handed, finished in await self._db.jobs(acting.org_id, limit=limit)
            ]
        )

    # ── The node agent ──────────────────────────────────────────────

    async def pull(self, org_id: str, producer: ProducerRef, *, handed_to: str) -> PulledCommands:
        await self._producers.exists(org_id, producer)
        vm = await self._producers.placement(org_id, producer)
        rows = await self._db.pull(
            org_id,
            producer_kind=producer.kind,
            producer_id=producer.id,
            in_scope=lambda job: self._scopes.reaches(job, vm),
            handed_to=handed_to,
        )
        return PulledCommands(commands=[VmCommandRecords.handed_out(row) for row in rows])

    async def ack(self, org_id: str, command_id: str, started_at: datetime | None) -> None:
        if not await self._db.ack(org_id, command_id, started_at=started_at):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Not a command handed out and waiting')

    async def finish(self, org_id: str, command_id: str, result: CommandResult) -> None:
        outcome = CommandOutcome(
            exit_code=result.exit_code,
            output=result.output[-MAX_OUTPUT:] if result.output else result.output,
            error=result.error,
            finished_at=result.finished_at or datetime.now(timezone.utc),
            started_at=result.started_at,
        )
        if not await self._db.finish(org_id, command_id, outcome):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Not a command handed out and unfinished')

    async def wakes(self) -> list[WakeReason]:
        """Every producer, in every organisation, with a command for its VM alone waiting."""
        return [
            WakeReason(org_id=org_id, producer=ProducerRef.stored(kind, producer_id), reasons=['command'])
            for org_id in await self._tenants.active_org_ids()
            for kind, producer_id in await self._db.waiting_to_wake(org_id)
        ]
