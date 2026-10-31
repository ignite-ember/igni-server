"""Commands as root in producers' microVMs, and jobs for many of them.

Who may run what, exactly (`docs/service-identity.md` §13, igni's
`docs/CLOUD.md` §7):

* a producer in its own VM — a person with their session, an app with
  its token;
* an org admin in any VM of their organisation;
* the super admin in any VM of the platform.

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
from pydantic import BaseModel

from app.api.base_v1.schema.vm_commands import (
    CommandResult,
    HandedOutCommand,
    JobScope,
    MAX_OUTPUT,
    PulledCommands,
    QueuedJobs,
    QueueJobRequest,
    Requester,
    RunCommandRequest,
    VmCommandList,
    VmCommandRecord,
    VmJobList,
    VmJobRecord,
    WakeReason,
)
from app.db.sql.apps import AppsDB
from app.db.sql.groups import GroupsDB
from app.db.sql.models.app_space import SPACE
from app.db.sql.models.registered_client import APP
from app.db.sql.models.vm_command import VmCommand, VmJob
from app.db.sql.organizations import OrganizationDB
from app.db.sql.spaces import SpacesDB
from app.db.sql.team import TeamDB
from app.db.sql.tenancy import TenantData
from app.db.sql.vm_commands import VmCommandsDB
from app.db.sql.vm_profiles import VmProfilesDB
from app.security.producer import ProducerCaller, ProducerRef
from app.services.auth.jwt_service import PERSON
from app.services.cloud.vm_profiles import VmProfiles
from app.services.portal.group_extensions import GroupExtensions

PRODUCER = 'producer'
ORG_ADMIN = 'org_admin'
SUPER_ADMIN = 'super_admin'


class Acting(BaseModel):
    """Who is asking, in which organisation, and as what."""

    org_id: str
    requester: Requester


class VmCommandAuthority:
    """The permission rules, in one place."""

    @staticmethod
    def _org(caller: ProducerCaller, org_id: str | None) -> str:
        org = org_id or caller.org_id
        if not org:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Name the organisation')
        return org

    @staticmethod
    def _requester(caller: ProducerCaller, acting_as: str) -> Requester:
        return Requester(kind=caller.kind, id=caller.id, acting_as=acting_as)

    async def over_one(self, caller: ProducerCaller, org_id: str | None, target: ProducerRef) -> Acting:
        org = self._org(caller, org_id)
        if org == caller.org_id and target == caller.ref:
            return Acting(org_id=org, requester=self._requester(caller, PRODUCER))
        if org == caller.org_id and caller.is_org_admin:
            return Acting(org_id=org, requester=self._requester(caller, ORG_ADMIN))
        if caller.is_super_admin:
            return Acting(org_id=org, requester=self._requester(caller, SUPER_ADMIN))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='A producer runs commands only in its own VM; an org admin in their organisation',
        )

    async def over_many(self, caller: ProducerCaller, org_id: str | None, *, platform: bool = False) -> Acting:
        if platform:
            if not caller.is_super_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail='Only the super admin acts platform-wide'
                )
            return Acting(org_id='', requester=self._requester(caller, SUPER_ADMIN))
        org = self._org(caller, org_id)
        if org == caller.org_id and caller.is_org_admin:
            return Acting(org_id=org, requester=self._requester(caller, ORG_ADMIN))
        if caller.is_super_admin:
            return Acting(org_id=org, requester=self._requester(caller, SUPER_ADMIN))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail='Only an org admin, or the super admin, acts on many VMs'
        )


class Producer:
    """What a producer is, for deciding which jobs reach its VM."""

    @staticmethod
    async def exists(org_id: str, producer: ProducerRef) -> None:
        if await OrganizationDB().get_by_id(org_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Organisation not found')
        if producer.kind == APP:
            found = await AppsDB().get(org_id, producer.id) is not None
        elif producer.kind == SPACE:
            found = await SpacesDB().get(org_id, producer.id) is not None
        else:
            found = await TeamDB().get_active_member(org_id, producer.id) is not None
        if not found:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such producer in this organisation')

    @staticmethod
    async def scope_of(org_id: str, producer: ProducerRef) -> tuple[str | None, str]:
        """The producer's group (a person's; None for an app or a space) and its effective profile id."""
        group = None if producer.kind != PERSON else await GroupExtensions().group_of(producer.id, org_id)
        profiles = VmProfiles()
        assigned = await profiles.assigned(org_id, producer)
        if assigned is None or await VmProfilesDB().get(org_id, assigned) is None:
            assigned = (await profiles.default(org_id)).id
        return group, assigned


class Records:
    @staticmethod
    def command(org_id: str, row: VmCommand) -> VmCommandRecord:
        return VmCommandRecord(
            command_id=row.id,
            org_id=org_id,
            producer=ProducerRef(kind=row.producer_kind, id=row.producer_id),  # type: ignore[arg-type]
            command=row.command,
            timeout_s=row.timeout_s,
            job_id=row.job_id,
            wake=row.wake,
            requested_by=Requester(kind=row.requested_by_kind, id=row.requested_by_id, acting_as=row.requested_as),
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

    @staticmethod
    def job(org_id: str, job: VmJob, handed: int = 0, finished: int = 0) -> VmJobRecord:
        return VmJobRecord(
            job_id=job.id,
            org_id=org_id,
            scope=JobScope(kind=job.scope_kind, id=job.scope_id),  # type: ignore[arg-type]
            command=job.command,
            timeout_s=job.timeout_s,
            requested_by=Requester(kind=job.requested_by_kind, id=job.requested_by_id, acting_as=job.requested_as),
            created_at=job.created_at,
            cancelled_at=job.cancelled_at,
            handed_out=handed,
            finished=finished,
        )


class VmCommands:
    """What people and apps ask for; what the node agent pulls and reports."""

    def __init__(self) -> None:
        self._db = VmCommandsDB()
        self._authority = VmCommandAuthority()

    # ── Asking ──────────────────────────────────────────────────────

    async def run(self, caller: ProducerCaller, body: RunCommandRequest) -> VmCommandRecord:
        acting = await self._authority.over_one(caller, body.org_id, body.producer)
        await Producer.exists(acting.org_id, body.producer)
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
        return Records.command(acting.org_id, row)

    async def _check_scope(self, org_id: str, scope: JobScope) -> None:
        if scope.kind == 'group':
            if not scope.id or await GroupsDB().get_group(org_id, scope.id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Group not found')
        elif scope.kind == 'profile':
            if not scope.id or await VmProfilesDB().get(org_id, scope.id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='VM profile not found')
        elif scope.id is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'A {scope.kind} job names no id')

    async def queue(self, caller: ProducerCaller, body: QueueJobRequest) -> QueuedJobs:
        platform = body.scope.kind == 'platform'
        acting = await self._authority.over_many(caller, body.org_id, platform=platform)
        job_id = str(uuid.uuid4())
        if platform:
            # Every organisation there is now, provisioning a database
            # for one that has none yet: one created later is not reached.
            orgs = [str(org.id) for org, _ in await OrganizationDB().all_with_member_counts()]
        else:
            if await OrganizationDB().get_by_id(acting.org_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Organisation not found')
            await self._check_scope(acting.org_id, body.scope)
            orgs = [acting.org_id]
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
            queued.append(Records.job(org_id, job))
        return QueuedJobs(jobs=queued)

    async def cancel(self, caller: ProducerCaller, org_id: str | None, command_id: str) -> None:
        org = org_id or caller.org_id or ''
        row = await self._db.get(org, command_id) if org else None
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Command not found')
        await self._authority.over_one(caller, org, ProducerRef(kind=row.producer_kind, id=row.producer_id))  # type: ignore[arg-type]
        if not await self._db.cancel(org, command_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Already handed to the VM')

    async def cancel_job(self, caller: ProducerCaller, org_id: str | None, job_id: str) -> VmJobRecord:
        acting = await self._authority.over_many(caller, org_id)
        job = await self._db.cancel_job(acting.org_id, job_id, caller.id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Job not found')
        return Records.job(acting.org_id, job)

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
        org = org_id or caller.org_id or ''
        try:
            await self._authority.over_many(caller, org)
        except HTTPException:
            if org != caller.org_id:
                raise
            producer = caller.ref
        rows = await self._db.recent(
            org,
            producer_kind=producer.kind if producer else None,
            producer_id=producer.id if producer else None,
            job_id=job_id,
            limit=limit,
        )
        return VmCommandList(commands=[Records.command(org, row) for row in rows])

    async def jobs(self, caller: ProducerCaller, org_id: str | None, limit: int) -> VmJobList:
        acting = await self._authority.over_many(caller, org_id)
        return VmJobList(
            jobs=[
                Records.job(acting.org_id, job, handed, finished)
                for job, handed, finished in await self._db.jobs(acting.org_id, limit=limit)
            ]
        )

    # ── The node agent ──────────────────────────────────────────────

    async def pull(self, org_id: str, producer: ProducerRef, *, handed_to: str) -> PulledCommands:
        await Producer.exists(org_id, producer)
        group, profile = await Producer.scope_of(org_id, producer)

        def in_scope(job: VmJob) -> bool:
            if job.scope_kind in ('org', 'platform'):
                return True
            if job.scope_kind == 'group':
                return group is not None and job.scope_id == group
            return job.scope_id == profile

        rows = await self._db.pull(
            org_id, producer_kind=producer.kind, producer_id=producer.id, in_scope=in_scope, handed_to=handed_to
        )
        return PulledCommands(
            commands=[
                HandedOutCommand(
                    command_id=row.id,
                    command=row.command,
                    timeout_s=row.timeout_s,
                    job_id=row.job_id,
                    requested_by=Requester(
                        kind=row.requested_by_kind, id=row.requested_by_id, acting_as=row.requested_as
                    ),
                    created_at=row.created_at,
                )
                for row in rows
            ]
        )

    async def ack(self, org_id: str, command_id: str, started_at: datetime | None) -> None:
        if not await self._db.ack(org_id, command_id, started_at=started_at):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Not a command handed out and waiting')

    async def finish(self, org_id: str, command_id: str, result: CommandResult) -> None:
        output = result.output[-MAX_OUTPUT:] if result.output else result.output
        done = await self._db.finish(
            org_id,
            command_id,
            {
                'exit_code': result.exit_code,
                'output': output,
                'error': result.error,
                'finished_at': result.finished_at or datetime.now(timezone.utc),
                **({'started_at': result.started_at} if result.started_at else {}),
            },
        )
        if not done:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Not a command handed out and unfinished')

    async def wakes(self) -> list[WakeReason]:
        """Every producer, in every organisation, with a command for its VM alone waiting."""
        found = []
        for org_id in await TenantData().each_tenant():
            for kind, producer_id in await self._db.waiting_to_wake(org_id):
                found.append(
                    WakeReason(
                        org_id=org_id,
                        producer=ProducerRef(kind=kind, id=producer_id),  # type: ignore[arg-type]
                        reasons=['command'],
                    )
                )
        return found
