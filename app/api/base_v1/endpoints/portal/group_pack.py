"""What a signed-in person's CLI reads about their own group: the group, the tool names, and the pack."""

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.base_v1.schema.portal.group_entries import AgentToolsResponse
from app.api.base_v1.schema.portal.group_pack import SignedNoPack, SignedOrgGroupPack
from app.api.base_v1.schema.portal.groups import OrgGroupSummary
from app.api.base_v1.schema.portal.user import PortalAccount, PortalUser
from app.security.portal_auth import get_current_account, get_current_portal_user
from app.services.portal.agent_tools import KNOWN_TOOLS
from app.services.portal.group_members_service import GroupMembersService
from app.services.portal.group_pack_delivery import GroupPackDelivery
from app.services.portal.group_refusals import GroupRefusals

router = APIRouter()


@router.get(
    '/me/group',
    response_model=OrgGroupSummary | None,
    status_code=status.HTTP_200_OK,
)
async def get_my_group(
    current_user: PortalUser = Depends(get_current_portal_user),
) -> OrgGroupSummary | None:
    """Get the group the current user belongs to. Any authenticated org member.

    Scoped to the caller's own org like every other path: a user could be
    in a group under a different org, and this endpoint answers about the
    one they are asking as.
    """
    with GroupRefusals():
        return await GroupMembersService().get_user_group(org_id=current_user.org_id, user_id=current_user.user_id)


@router.get(
    '/agent-tools',
    response_model=AgentToolsResponse,
    status_code=status.HTTP_200_OK,
)
async def list_agent_tools(
    current_user: PortalUser = Depends(get_current_portal_user),
) -> AgentToolsResponse:
    """The tool names an agent can ask for.

    Served rather than shipped in the bundle so the portal validates
    against the same list the write path checks, instead of a second
    copy that can disagree with it.
    """
    return AgentToolsResponse(tools=sorted(KNOWN_TOOLS))


@router.get(
    '/me/group/pack',
    response_model=SignedOrgGroupPack | SignedNoPack,
    status_code=status.HTTP_200_OK,
)
async def get_my_group_pack(
    request: Request,
    current_user: PortalAccount = Depends(get_current_account),
) -> Response:
    """The current user's group pack, signed. Any signed-in account.

    An account rather than an org member: igni asks for this on every
    session, and an account in no organisation yet is a normal state
    that has no pack, not a refusal for the CLI to report. It gets a
    signed "no pack" so igni can clear the policy it cached.

    Revalidates: a client that sends back the ``ETag`` it holds gets a
    304 and no body when nothing has changed (see ``GroupPackDelivery``).

    Revalidation, not caching: every response leaves here with
    ``no-store`` (see ``DisableCacheMiddleware``), so a ``Cache-Control``
    set here would not be delivered. ``no-store`` stops a cache *storing*
    the pack and leaves conditional revalidation alone, so the client asks
    every time and the saving is in the answer, not in the asking.
    """
    with GroupRefusals():
        answer = await GroupPackDelivery().answer(
            user_id=current_user.user_id,
            org_id=current_user.org_id,
            held_etag=request.headers.get('if-none-match'),
        )
    if answer.body is None:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={'ETag': answer.etag})
    return JSONResponse(content=answer.body.model_dump(mode='json'), headers={'ETag': answer.etag})
