"""Portal group entries: the agents, prompts and settings a group gives its people."""

from fastapi import APIRouter, Depends, status

from app.api.base_v1.schema.portal.group_entries import (
    CreateEntryRequest,
    OrgGroupEntryList,
    UpdateEntryRequest,
)
from app.api.base_v1.schema.portal.user import PortalUser
from app.security.portal_auth import require_admin
from app.services.portal.entry_warnings import EntryWarnings
from app.services.portal.group_entries_service import GroupEntriesService
from app.services.portal.group_refusals import GroupRefusals

router = APIRouter()


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
