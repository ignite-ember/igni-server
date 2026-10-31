"""Commands in producers' VMs: asking for them, jobs, and the audit log.

A person's session or an app's token (`require_producer`). Who may run
what is `VmCommandAuthority`'s, and `docs/service-identity.md` §13.
"""

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.base_v1.schema.vm_commands import (
    QueuedJobs,
    QueueJobRequest,
    RunCommandRequest,
    VmCommandList,
    VmCommandRecord,
    VmJobList,
    VmJobRecord,
)
from app.middlewares.audit import set_audit_detail, set_audit_target
from app.security.producer import ProducerCaller, ProducerKind, ProducerRef, require_producer
from app.services.cloud.vm_commands import VmCommands

router = APIRouter(prefix='/vm')


@router.post('/commands', response_model=VmCommandRecord, status_code=status.HTTP_202_ACCEPTED)
async def run_command(
    request: Request, body: RunCommandRequest, caller: ProducerCaller = Depends(require_producer)
) -> VmCommandRecord:
    """Run a command as root in one producer's VM. Its VM is woken for it."""
    record = await VmCommands().run(caller, body)
    set_audit_target(request, target_type='vm_command', target_id=record.command_id)
    set_audit_detail(request, {'producer': f'{body.producer.kind}:{body.producer.id}', 'org': record.org_id})
    return record


@router.get('/commands', response_model=VmCommandList)
async def command_log(
    org_id: str | None = None,
    producer_kind: ProducerKind | None = None,
    producer_id: str | None = None,
    job_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    caller: ProducerCaller = Depends(require_producer),
) -> VmCommandList:
    """The audit log, newest first: an organisation's for its admins and the super admin, a producer's own otherwise."""
    producer = ProducerRef(kind=producer_kind, id=producer_id) if producer_kind and producer_id else None
    return await VmCommands().log(caller, org_id=org_id, producer=producer, job_id=job_id, limit=limit)


@router.post('/commands/{command_id}/cancel', status_code=status.HTTP_204_NO_CONTENT)
async def cancel_command(
    command_id: str, org_id: str | None = None, caller: ProducerCaller = Depends(require_producer)
) -> Response:
    """Cancel a command its VM has not been handed yet."""
    await VmCommands().cancel(caller, org_id, command_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post('/jobs', response_model=QueuedJobs, status_code=status.HTTP_202_ACCEPTED)
async def queue_job(
    request: Request, body: QueueJobRequest, caller: ProducerCaller = Depends(require_producer)
) -> QueuedJobs:
    """One command for many VMs, each handed it at its next wake. Wakes nobody."""
    queued = await VmCommands().queue(caller, body)
    if queued.jobs:
        set_audit_target(request, target_type='vm_job', target_id=queued.jobs[0].job_id)
    set_audit_detail(request, {'scope': body.scope.kind, 'scope_id': body.scope.id, 'organisations': len(queued.jobs)})
    return queued


@router.get('/jobs', response_model=VmJobList)
async def list_jobs(
    org_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    caller: ProducerCaller = Depends(require_producer),
) -> VmJobList:
    return await VmCommands().jobs(caller, org_id, limit)


@router.post('/jobs/{job_id}/cancel', response_model=VmJobRecord)
async def cancel_job(
    job_id: str, org_id: str | None = None, caller: ProducerCaller = Depends(require_producer)
) -> VmJobRecord:
    """Stop handing it out. VMs already handed it finish it."""
    return await VmCommands().cancel_job(caller, org_id, job_id)
