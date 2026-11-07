"""Portal group management — groups, members, entries, and CLI pack."""

import hashlib

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.base_v1.schema.portal.group_entries import (
    AgentToolsResponse,
    CreateEntryRequest,
    OrgGroupEntryList,
    UpdateEntryRequest,
)
from app.api.base_v1.schema.portal.group_pack import SignedOrgGroupPack
from app.api.base_v1.schema.portal.groups import (
    AddMemberRequest,
    CopyGroupRequest,
    CreateGroupRequest,
    GroupExtensionList,
    OrgGroupDetail,
    OrgGroupList,
    OrgGroupMemberList,
    OrgGroupMembershipList,
    OrgGroupResponse,
    OrgGroupSummary,
    SetGroupExtension,
    UpdateGroupRequest,
)
from app.api.base_v1.schema.portal.user import PortalAccount, PortalUser
from app.security.portal_auth import get_current_account, get_current_portal_user, require_admin
from app.services.portal.agent_tools import KNOWN_TOOLS
from app.services.portal.entry_warnings import EntryWarnings
from app.services.portal.group_entries_service import GroupEntriesService
from app.services.portal.group_extensions import GroupExtensions
from app.services.portal.group_members_service import GroupMembersService
from app.services.portal.group_pack_service import GroupPackService
from app.services.portal.group_pack_signature import GroupPackSigner
from app.services.portal.group_refusals import GroupRefusals
from app.services.portal.groups_service import GroupsService

router = APIRouter()


# -------------------------------------------------------------------------
# Groups
# -------------------------------------------------------------------------


@router.get('/groups', response_model=OrgGroupList, status_code=status.HTTP_200_OK)
async def list_groups(
    offset: int = 0,
    limit: int | None = None,
    current_user: PortalUser = Depends(get_current_portal_user),
) -> OrgGroupList:
    """List all groups in the org. Any authenticated org member.

    Readable by members, not just admins: ``test_list_groups_as_member``
    asserts a 200 for a plain member while
    ``test_create_group_non_admin_forbidden`` asserts a 403 for the same user
    creating one — reads open, writes admin-only. The result is scoped to
    ``current_user.org_id``, so a member only ever sees their own org.
    """
    with GroupRefusals():
        return await GroupsService().list_groups(
            org_id=current_user.org_id,
            offset=offset,
            limit=limit,
            by_user_id=current_user.user_id,
        )


@router.post('/groups', response_model=OrgGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: CreateGroupRequest,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupResponse:
    """Create a new group. Admin-only."""
    with GroupRefusals():
        return await GroupsService().create_group(
            org_id=current_user.org_id, body=body, by_user_id=current_user.user_id
        )


@router.get('/groups/{group_id}', response_model=OrgGroupDetail, status_code=status.HTTP_200_OK)
async def get_group(
    group_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupDetail:
    """Get a single group with member and entry counts. Admin-only."""
    with GroupRefusals():
        return await GroupsService().get_group(
            org_id=current_user.org_id, group_id=group_id, by_user_id=current_user.user_id
        )


@router.patch('/groups/{group_id}', response_model=OrgGroupResponse, status_code=status.HTTP_200_OK)
async def update_group(
    group_id: str,
    body: UpdateGroupRequest,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupResponse:
    """Update a group's name or description. Admin-only."""
    with GroupRefusals():
        return await GroupsService().update_group(org_id=current_user.org_id, group_id=group_id, body=body)


@router.delete('/groups/{group_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> None:
    """Delete a group and its entries. Its people go back to the Default group. Admin-only."""
    with GroupRefusals():
        await GroupsService().delete_group(
            org_id=current_user.org_id, group_id=group_id, by_user_id=current_user.user_id
        )


@router.post('/groups/{group_id}/copy', response_model=OrgGroupResponse, status_code=status.HTTP_201_CREATED)
async def copy_group(
    group_id: str,
    body: CopyGroupRequest,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupResponse:
    """Copy a group's entries, extensions and settings into a new group with nobody in it. Admin-only."""
    with GroupRefusals():
        return await GroupsService().copy_group(
            org_id=current_user.org_id, group_id=group_id, body=body, by_user_id=current_user.user_id
        )


# -------------------------------------------------------------------------
# Members
# -------------------------------------------------------------------------


@router.get('/group-memberships', response_model=OrgGroupMembershipList, status_code=status.HTTP_200_OK)
async def list_group_memberships(
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupMembershipList:
    """Every active person of the organisation and the one group they are in. Admin-only."""
    with GroupRefusals():
        return await GroupMembersService().memberships(org_id=current_user.org_id, by_user_id=current_user.user_id)


@router.get('/groups/{group_id}/members', response_model=OrgGroupMemberList, status_code=status.HTTP_200_OK)
async def list_group_members(
    group_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupMemberList:
    """List all members of a group. Admin-only, and admin *of this org*.

    ``require_admin`` proves the caller is an administrator of some
    organization, not of the one that owns this group — passing
    ``org_id`` is what turns the first into the second.
    """
    with GroupRefusals():
        return await GroupMembersService().list_members(
            org_id=current_user.org_id, group_id=group_id, by_user_id=current_user.user_id
        )


@router.post(
    '/groups/{group_id}/members',
    response_model=OrgGroupMemberList,
    status_code=status.HTTP_200_OK,
)
async def add_group_member(
    group_id: str,
    body: AddMemberRequest,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupMemberList:
    """Put an org member in this group, moving them out of their current one. Admin-only.

    A person is in exactly one group, so this is the only way into one:
    there is no separate "move".
    """
    members = GroupMembersService()
    with GroupRefusals():
        await members.add_member(
            org_id=current_user.org_id, group_id=group_id, user_id=body.user_id, by_user_id=current_user.user_id
        )
        # Return updated member list, scoped like the mutation above it.
        return await members.list_members(org_id=current_user.org_id, group_id=group_id)


@router.delete(
    '/groups/{group_id}/members/{user_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_group_member(
    group_id: str,
    user_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> None:
    """Take a person out of this group, back into the Default one. Admin-only.

    409 on the Default group itself: nobody leaves it except by being
    put in another group.
    """
    with GroupRefusals():
        await GroupMembersService().remove_member(
            org_id=current_user.org_id, group_id=group_id, user_id=user_id, by_user_id=current_user.user_id
        )


@router.get(
    '/users/{user_id}/group',
    response_model=OrgGroupSummary | None,
    status_code=status.HTTP_200_OK,
)
async def get_user_group(
    user_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupSummary | None:
    """Get the group a user belongs to. Admin-only, scoped to this org."""
    with GroupRefusals():
        return await GroupMembersService().get_user_group(org_id=current_user.org_id, user_id=user_id)


# -------------------------------------------------------------------------
# Entrys
# -------------------------------------------------------------------------


@router.get(
    '/groups/{group_id}/entries',
    response_model=OrgGroupEntryList,
    status_code=status.HTTP_200_OK,
)
async def list_entries(
    group_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupEntryList:
    """List all entries for a group. Admin-only."""
    with GroupRefusals():
        return await GroupEntriesService().list_entries(org_id=current_user.org_id, group_id=group_id)


@router.post(
    '/groups/{group_id}/entries',
    response_model=OrgGroupEntryList,
    status_code=status.HTTP_201_CREATED,
)
async def create_entry(
    group_id: str,
    body: CreateEntryRequest,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupEntryList:
    """Create a new entry in a group. Admin-only.

    Answers with the group's whole list, plus anything worth saying
    about what was written — a mistyped tool name does not stop the
    write, but the admin should hear about it now rather than when an
    agent fails to start.
    """
    entries = GroupEntriesService()
    with GroupRefusals():
        await entries.create_entry(
            org_id=current_user.org_id, group_id=group_id, body=body, by_user_id=current_user.user_id
        )
        listing = await entries.list_entries(org_id=current_user.org_id, group_id=group_id)
        listing.warnings = await EntryWarnings().warnings_for(
            kind=body.kind,
            content=body.content,
            org_id=current_user.org_id,
            group_id=group_id,
            entry_name=body.entry_name,
        )
    return listing


@router.patch(
    '/groups/{group_id}/entries/{entry_id}',
    response_model=OrgGroupEntryList,
    status_code=status.HTTP_200_OK,
)
async def update_entry(
    group_id: str,
    entry_id: str,
    body: UpdateEntryRequest,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupEntryList:
    """Update an entry's content or enabled state. Admin-only."""
    entries = GroupEntriesService()
    with GroupRefusals():
        await entries.update_entry(org_id=current_user.org_id, group_id=group_id, entry_id=entry_id, body=body)
        listing = await entries.list_entries(org_id=current_user.org_id, group_id=group_id)
        if body.content is not None:
            entry = await entries.get_entry(org_id=current_user.org_id, group_id=group_id, entry_id=entry_id)
            listing.warnings = await EntryWarnings().warnings_for(
                kind=str(entry.kind),
                content=body.content,
                org_id=current_user.org_id,
                group_id=group_id,
                entry_name=str(entry.entry_name),
            )
    return listing


@router.delete(
    '/groups/{group_id}/entries/{entry_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_entry(
    group_id: str,
    entry_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> None:
    """Delete an entry. Admin-only."""
    with GroupRefusals():
        await GroupEntriesService().delete_entry(org_id=current_user.org_id, group_id=group_id, entry_id=entry_id)


# -------------------------------------------------------------------------
# Extensions — which approved ones this group's people get (off by default)
# -------------------------------------------------------------------------


@router.get(
    '/groups/{group_id}/extensions',
    response_model=GroupExtensionList,
    status_code=status.HTTP_200_OK,
)
async def list_group_extensions(
    group_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> GroupExtensionList:
    """Every approved extension, each with whether this group has it on. Admin-only."""
    return await GroupExtensions().of_group(current_user.org_id, group_id)


@router.put(
    '/groups/{group_id}/extensions/{extension_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_group_extension(
    group_id: str,
    extension_id: str,
    body: SetGroupExtension,
    current_user: PortalUser = Depends(require_admin),
) -> None:
    """Switch an approved extension on or off for this group's people. Admin-only."""
    await GroupExtensions().set(
        current_user.org_id, group_id, extension_id, enabled=body.enabled, by_user_id=current_user.user_id
    )


# -------------------------------------------------------------------------
# CLI pack — org member access (not admin-gated)
# -------------------------------------------------------------------------


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
    response_model=SignedOrgGroupPack | None,
    status_code=status.HTTP_200_OK,
)
async def get_my_group_pack(
    request: Request,
    current_user: PortalAccount = Depends(get_current_account),
) -> Response | None:
    """The current user's group pack. Any signed-in account.

    An account rather than an org member: igni asks for this on every
    session, and an account in no organisation yet is a normal state
    that has no pack, not a refusal for the CLI to report.

    Revalidates: a client that sends back the ``ETag`` it holds gets a
    304 and no body when nothing has changed. igni polls this every few
    minutes from every running session, and the answer is almost always
    "same as before" — a full pack of agent prompts each time would be
    a lot of bytes to say nothing.

    Revalidation, not caching: every response leaves here with
    ``no-store`` (see ``DisableCacheMiddleware``), so the client asks
    every time and the saving is in the answer, not in the asking.
    """
    # The group in the organisation this session is in, which is also the
    # `org` the signed pack names and igni checks against the token.
    with GroupRefusals():
        pack = await GroupPackService().get_my_group_pack(user_id=current_user.user_id, org_id=current_user.org_id)
    signer = GroupPackSigner()
    if pack is None:
        # "No pack for you", signed for this person, so igni can clear the
        # policy it cached (an unsigned `null` it could only ignore). Older
        # clients read this as a pack they cannot parse and keep theirs,
        # which is what they did with `null`.
        absent_binding = signer.binding(subject=current_user.user_id, org_id=current_user.org_id)
        absent_tag = '"' + hashlib.md5(f'no-pack|{absent_binding}'.encode(), usedforsecurity=False).hexdigest() + '"'
        if request.headers.get('if-none-match') == absent_tag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={'ETag': absent_tag})
        return JSONResponse(
            content={
                'no_pack': True,
                'signed_pack': signer.sign_absence(subject=current_user.user_id, org_id=current_user.org_id),
            },
            headers={'ETag': absent_tag},
        )

    # Both on the model: what the pack serialises to, and what
    # identifies it, are properties of the pack and not of this
    # handler. They were twenty lines of plumbing here, half of it
    # explaining bugs that had already been fixed.
    payload = pack.as_payload()
    # Signed for this person (`sub`) with the key igni signs its tokens
    # with: igni applies a cached pack only if this signature verifies
    # (see `GroupPackSigner`). The ETag is bound to what the signature
    # depends on, so a 304 never leaves a client holding a pack signed
    # for somebody else or with a key that has since been rotated out.
    binding = signer.binding(subject=current_user.user_id, org_id=pack.org_id)
    etag = pack.etag(bound_to=binding)

    if request.headers.get('if-none-match') == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={'ETag': etag})

    # ETag only. `DisableCacheMiddleware` sets `Cache-Control:
    # no-store` on every response, so a `Cache-Control` set here would
    # not be delivered — and `no-store` is the right answer for an API
    # serving per-user data anyway. It costs nothing here: `no-store`
    # stops a cache *storing* the pack, and leaves conditional
    # revalidation alone, so the tag still turns the common "nothing
    # has changed" poll into a 304 with no body.
    payload['signed_pack'] = signer.sign(pack, subject=current_user.user_id, org_id=pack.org_id)
    return JSONResponse(content=payload, headers={'ETag': etag})
