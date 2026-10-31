"""Commands and jobs in producers' VMs, in the organisation's own database.

`vm_commands` is both the queue the node agent pulls from and the audit
log; `vm_jobs` holds the commands for many VMs, which become a row of
`vm_commands` per VM as each wakes (`docs/service-identity.md` §13).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.sql.models.vm_command import CANCELLED, FINISHED, HANDED_OUT, QUEUED, RUNNING, VmCommand, VmJob
from app.db.sql.tenancy import TenantData

#: A command handed out and not acknowledged or finished within this is
#: handed out again at the VM's next pull: its node agent died with it.
LEASE = timedelta(minutes=10)


class VmCommandsDB(TenantData):
    async def add(self, org_id: str, command: VmCommand) -> VmCommand:
        async with self.session_scope(org_id) as session:
            session.add(command)
        return command

    async def get(self, org_id: str, command_id: str) -> VmCommand | None:
        async with self.session_scope(org_id) as session:
            return await session.get(VmCommand, command_id)

    async def recent(
        self,
        org_id: str,
        *,
        producer_kind: str | None = None,
        producer_id: str | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[VmCommand]:
        query = select(VmCommand)
        if producer_kind:
            query = query.where(VmCommand.producer_kind == producer_kind)
        if producer_id:
            query = query.where(VmCommand.producer_id == producer_id)
        if job_id:
            query = query.where(VmCommand.job_id == job_id)
        async with self.session_scope(org_id) as session:
            rows = await session.execute(query.order_by(VmCommand.created_at.desc()).limit(limit))
            return list(rows.scalars().all())

    async def cancel(self, org_id: str, command_id: str) -> bool:
        """Cancel a command nobody has picked up yet."""
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                update(VmCommand)
                .where(VmCommand.id == command_id, VmCommand.state == QUEUED)
                .values(state=CANCELLED, finished_at=datetime.now(timezone.utc))
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def add_job(self, org_id: str, job: VmJob) -> VmJob:
        async with self.session_scope(org_id) as session:
            session.add(job)
        return job

    async def get_job(self, org_id: str, job_id: str) -> VmJob | None:
        async with self.session_scope(org_id) as session:
            return await session.get(VmJob, job_id)

    async def jobs(self, org_id: str, *, limit: int = 100) -> list[tuple[VmJob, int, int]]:
        """Recent jobs, each with how many VMs were handed it and how many finished it."""
        handed = func.count(VmCommand.id)
        finished = func.count(VmCommand.id).filter(VmCommand.state == FINISHED)
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(VmJob, handed, finished)
                .outerjoin(VmCommand, VmCommand.job_id == VmJob.id)
                .group_by(VmJob.id)
                .order_by(VmJob.created_at.desc())
                .limit(limit)
            )
            return [(job, int(h), int(f)) for job, h, f in rows.all()]

    async def cancel_job(self, org_id: str, job_id: str, by_id: str) -> VmJob | None:
        async with self.session_scope(org_id) as session:
            job = await session.get(VmJob, job_id)
            if job is not None and job.cancelled_at is None:
                job.cancelled_at = datetime.now(timezone.utc)
                job.cancelled_by_id = by_id
            return job

    async def pull(
        self,
        org_id: str,
        *,
        producer_kind: str,
        producer_id: str,
        in_scope: Callable[[VmJob], bool],
        handed_to: str,
    ) -> list[VmCommand]:
        """Hand the node agent everything waiting for this VM, in the order it was asked for.

        Commands for this VM that are queued, or were handed out and
        never finished within the lease; and every active job in its
        scope that it has not been handed yet, each now a row of its own.
        """
        now = datetime.now(timezone.utc)
        mine = and_(VmCommand.producer_kind == producer_kind, VmCommand.producer_id == producer_id)
        async with self.session_scope(org_id) as session:
            done = select(VmCommand.job_id).where(mine, VmCommand.job_id.is_not(None))
            jobs = (
                (
                    await session.execute(
                        select(VmJob)
                        .where(VmJob.cancelled_at.is_(None), VmJob.id.not_in(done))
                        .order_by(VmJob.created_at)
                    )
                )
                .scalars()
                .all()
            )
            for job in jobs:
                if in_scope(job):
                    await session.execute(
                        insert(VmCommand)
                        .values(
                            producer_kind=producer_kind,
                            producer_id=producer_id,
                            command=job.command,
                            timeout_s=job.timeout_s,
                            job_id=job.id,
                            wake=False,
                            requested_by_kind=job.requested_by_kind,
                            requested_by_id=job.requested_by_id,
                            requested_as=job.requested_as,
                            state=QUEUED,
                            created_at=now,
                        )
                        .on_conflict_do_nothing(index_elements=['job_id', 'producer_kind', 'producer_id'])
                    )
            waiting = (
                (
                    await session.execute(
                        select(VmCommand)
                        .where(
                            mine,
                            or_(
                                VmCommand.state == QUEUED,
                                and_(VmCommand.state.in_((HANDED_OUT, RUNNING)), VmCommand.handed_out_at < now - LEASE),
                            ),
                        )
                        .order_by(VmCommand.created_at)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for command in waiting:
                command.state = HANDED_OUT
                command.handed_out_at = now
                command.handed_to = handed_to
            return list(waiting)

    async def ack(self, org_id: str, command_id: str, *, started_at: datetime | None) -> bool:
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                update(VmCommand)
                .where(VmCommand.id == command_id, VmCommand.state == HANDED_OUT)
                .values(state=RUNNING, started_at=started_at or datetime.now(timezone.utc))
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def finish(self, org_id: str, command_id: str, values: dict[str, object]) -> bool:
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                update(VmCommand)
                .where(VmCommand.id == command_id, VmCommand.state.in_((HANDED_OUT, RUNNING)))
                .values(state=FINISHED, **values)
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def waiting_to_wake(self, org_id: str) -> list[tuple[str, str]]:
        """Producers with a command for their VM alone waiting: (kind, id)."""
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(VmCommand.producer_kind, VmCommand.producer_id)
                .where(VmCommand.state == QUEUED, VmCommand.wake)
                .distinct()
            )
            return [(str(kind), str(pid)) for kind, pid in rows.all()]
