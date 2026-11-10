"""The node agent's side of commands, and the gateway's list of VMs to wake.

A platform token only. At every wake the node agent pulls what is
waiting for that VM, acknowledges each command as it starts it, and
posts each result; those three, and the request itself, are the audit
record (`docs/service-identity.md` §13).
"""

from datetime import datetime

from fastapi import APIRouter, Body, Depends, Response, status

from app.api.base_v1.schema.vm_commands import CommandResult, PulledCommands, Wakes
from app.security.platform_caller import PlatformCaller, require_platform
from app.security.producer import ProducerKind, ProducerRef
from app.services.cloud.vm_commands import VmCommands
from app.services.cloud.vm_profile_refusals import VmProfileRefusals
from app.services.cloud.wakes import WakeList

router = APIRouter()


@router.post('/orgs/{org_id}/producers/{kind}/{producer_id}/commands/pull', response_model=PulledCommands)
async def pull(
    org_id: str, kind: ProducerKind, producer_id: str, platform: PlatformCaller = Depends(require_platform)
) -> PulledCommands:
    """Everything waiting for this VM, oldest first, now handed out to the caller."""
    with VmProfileRefusals():
        return await VmCommands().pull(org_id, ProducerRef(kind=kind, id=producer_id), handed_to=platform.client_id)


@router.post('/orgs/{org_id}/vm-commands/{command_id}/ack', status_code=status.HTTP_204_NO_CONTENT)
async def ack(
    org_id: str,
    command_id: str,
    started_at: datetime | None = Body(default=None, embed=True),
    _platform: PlatformCaller = Depends(require_platform),
) -> Response:
    """The command has started in the VM."""
    await VmCommands().ack(org_id, command_id, started_at)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post('/orgs/{org_id}/vm-commands/{command_id}/result', status_code=status.HTTP_204_NO_CONTENT)
async def result(
    org_id: str, command_id: str, body: CommandResult, _platform: PlatformCaller = Depends(require_platform)
) -> Response:
    """The command has finished, or could not run."""
    await VmCommands().finish(org_id, command_id, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/wakes', response_model=Wakes)
async def wakes(_platform: PlatformCaller = Depends(require_platform)) -> Wakes:
    """Producers whose VMs should be woken now, and why."""
    return await WakeList().now()
