"""Service layer for portal group management (org-scoped)."""

from datetime import datetime, timezone
import json
import logging
from urllib.parse import urlparse

from fastapi import HTTPException, status

from app.api.base_v1.schema.portal.groups import (
    CopyGroupRequest,
    CreateEntryRequest,
    CreateGroupRequest,
    GroupTheme,
    OrgGroupDetail,
    OrgGroupEntryList,
    OrgGroupEntryResponse,
    OrgGroupList,
    OrgGroupMemberList,
    OrgGroupMemberResponse,
    OrgGroupMembership,
    OrgGroupMembershipList,
    OrgGroupPack,
    OrgGroupPackEntry,
    OrgGroupResponse,
    OrgGroupSummary,
    UpdateEntryRequest,
    UpdateGroupRequest,
)
from app.api.base_v1.schema.portal.user import PortalUser
from app.db.sql.groups import GroupsDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_group_entry import OrgGroupEntry
from app.db.sql.team import TeamDB
from app.db.sql.user import UserDB
from app.services.ai.pool_manager import PoolManager
from app.services.portal.agent_names import (
    frontmatter_name as agent_frontmatter_name,
    warning_for as agent_name_warning,
)
from app.services.portal.agent_tools import warning_for
from app.services.portal.default_group import DefaultGroup
from app.services.portal.entry_kinds import (
    ALLOWED_CONTENT_TYPE,
    KINDS_WITH_MODEL,
    VALID_CONTENT_TYPES,
    VALID_KINDS,
)
from app.services.portal.hook_scripts import warning_for as hook_script_warning

_db = GroupsDB()
_team_db = TeamDB()
_user_db = UserDB()

logger = logging.getLogger(__name__)


async def _refuse_unserved_model(model: str | None) -> None:
    """Reject a model no node in the chat pool answers to.

    Caught here rather than at request time because the failure is
    otherwise invisible until somebody in that group tries to use it —
    and it lands on them, not on the admin who made the typo. Adapter
    names are free text passed to ``--lora-modules``, so a typo is the
    likely mistake rather than an exotic one.
    """
    if not model:
        return

    try:
        pairs = await PoolManager().get_pool().list_available_models()
    except Exception as exc:  # noqa: BLE001 — a broken pool must not block group edits
        logger.warning('Could not check group model %r against the pool: %s', model, exc)
        return

    served = {name for name, _ in pairs}
    if model not in served:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'No inference node serves {model!r}. Available: {", ".join(sorted(served)) or "none"}.',
        )


class GroupsService:
    """Business logic for org groups, scoped to the active org."""

    @staticmethod
    async def _group_in_org(org_id: str, group_id: str) -> OrgGroup:
        """The group, or 404.

        Every entry path goes through this. They used to take a group id
        with no org, which meant an admin of one org could read or edit
        another org's entries by knowing the id — the group endpoints
        were scoped and the entry endpoints under them were not.
        """
        group = await _db.get_group(org_id, group_id)
        if not group:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Group not found')
        return group

    @staticmethod
    async def _writable_group(org_id: str, group_id: str) -> OrgGroup:
        """The group, or 404, or 409 if it is the standard set.

        The Default group is the copy of what igni ships and the thing
        every other group is started from. Editing it would leave an org
        with no reference point and nothing to copy — so it is read-only,
        and the way to have your own version of it is to copy it.

        409 rather than 403 on purpose: an admin is not lacking
        permission, the resource is immutable, and the portal needs to
        tell those apart to say anything useful.
        """
        group = await GroupsService._group_in_org(org_id, group_id)
        if bool(group.is_default):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    'This group holds what igni ships and cannot be changed. '
                    'Create a group from it to make your own version.'
                ),
            )
        return group

    @staticmethod
    def _response(group: OrgGroup) -> OrgGroupResponse:
        return OrgGroupResponse(
            id=group.id,
            org_id=group.org_id,
            name=group.name,
            description=group.description,
            default_model=group.default_model,
            theme=_theme_from_json(group.theme),
            is_default=bool(group.is_default),
            created_by_user_id=group.created_by_user_id,
            created_at=group.created_at,
            updated_at=group.updated_at,
        )

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    @staticmethod
    async def list_groups(
        org_id: str,
        offset: int = 0,
        limit: int | None = None,
        seed_default_for_user_id: str | None = None,
    ) -> OrgGroupList:
        """Every group in the org, with its member and entry counts.

        Seeds the Default group first when a user id is given, and puts
        everybody who is in no group yet into it. It used to appear only
        when somebody with no group asked for their pack, which meant the
        org most likely to want it as a template — one that already has
        groups — never got it. This is where an admin goes looking for
        it, so this is where it has to exist, holding the people it holds.
        """
        if seed_default_for_user_id:
            await DefaultGroup.place_everyone(org_id, by_user_id=seed_default_for_user_id)

        rows, total = await _db.list_groups(org_id, offset=offset, limit=limit)
        groups = []
        for g in rows:
            members = await _db.list_members(org_id, g.id)  # type: ignore[arg-type]
            entries = await _db.list_entries(org_id, g.id)  # type: ignore[arg-type]
            groups.append(
                OrgGroupDetail(
                    id=g.id,
                    org_id=g.org_id,
                    name=g.name,
                    description=g.description,
                    default_model=g.default_model,
                    theme=_theme_from_json(g.theme),
                    is_default=bool(g.is_default),
                    created_by_user_id=g.created_by_user_id,
                    created_at=g.created_at,
                    updated_at=g.updated_at,
                    member_count=len(members),
                    entry_count=len(entries),
                )
            )
        return OrgGroupList(groups=groups, total=total, offset=offset, limit=limit)

    @staticmethod
    async def create_group(body: CreateGroupRequest, current_user: PortalUser) -> OrgGroupResponse:
        existing = await _db.get_group_by_name(current_user.org_id, body.name)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A group named '{body.name}' already exists in this organization.",
            )

        await _refuse_unserved_model(body.default_model)

        if body.template_group_id:
            group = await _db.copy_group(
                org_id=current_user.org_id,
                from_group_id=body.template_group_id,
                name=body.name,
                description=body.description,
                default_model=body.default_model or None,
                theme=_theme_to_json(body.theme),
                created_by_user_id=current_user.user_id,
            )
        else:
            group = await _db.create_group(
                org_id=current_user.org_id,
                name=body.name,
                description=body.description,
                default_model=body.default_model or None,
                theme=_theme_to_json(body.theme),
                created_by_user_id=current_user.user_id,
            )
        return GroupsService._response(group)

    @staticmethod
    async def copy_group(group_id: str, body: CopyGroupRequest, current_user: PortalUser) -> OrgGroupResponse:
        """A new group that is a copy of this one: entries, extensions and settings, nobody in it.

        What a group holds is what its people get, so copying one is how
        to answer "the same as that team, plus this" without retyping
        it. The Default group — which holds what igni ships — copies like
        any other, and the copy is an ordinary, editable group.

        A copy, not a link. The new group is free to diverge, and the
        one it came from must not change under it later. People are not
        copied: each is in exactly one group, and stays where they are.
        """
        source = await GroupsService._group_in_org(current_user.org_id, group_id)
        if await _db.get_group_by_name(current_user.org_id, body.name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A group named '{body.name}' already exists in this organization.",
            )
        group = await _db.copy_group(
            org_id=current_user.org_id,
            from_group_id=group_id,
            name=body.name,
            description=body.description if body.description is not None else source.description,
            default_model=source.default_model,
            theme=source.theme,
            created_by_user_id=current_user.user_id,
        )
        return GroupsService._response(group)

    @staticmethod
    async def get_group(group_id: str, org_id: str, by_user_id: str | None = None) -> OrgGroupDetail:
        group = await _db.get_group(org_id, group_id)
        if not group:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Group not found')
        if by_user_id and bool(group.is_default):
            # Its count is everybody nobody has put elsewhere, so they
            # are placed before they are counted.
            await DefaultGroup.place_everyone(org_id, by_user_id=by_user_id)

        members = await _db.list_members(org_id, group_id)
        entries = await _db.list_entries(org_id, group_id)

        return OrgGroupDetail(
            id=group.id,
            org_id=group.org_id,
            name=group.name,
            description=group.description,
            default_model=group.default_model,
            theme=_theme_from_json(group.theme),
            is_default=bool(group.is_default),
            created_by_user_id=group.created_by_user_id,
            created_at=group.created_at,
            updated_at=group.updated_at,
            member_count=len(members),
            entry_count=len(entries),
        )

    @staticmethod
    async def update_group(
        group_id: str,
        body: UpdateGroupRequest,
        current_user: PortalUser,
    ) -> OrgGroupResponse:
        await GroupsService._writable_group(current_user.org_id, group_id)

        if body.name:
            existing = await _db.get_group_by_name(current_user.org_id, body.name)
            if existing and existing.id != group_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A group named '{body.name}' already exists in this organization.",
                )

        await _refuse_unserved_model(body.default_model)

        group = await _db.update_group(
            org_id=current_user.org_id,
            group_id=group_id,
            name=body.name,
            description=body.description,
            default_model=body.default_model or None,
            # An empty string is the explicit clear; absent leaves it,
            # because ``None`` already means "not supplied" here.
            clear_default_model=body.default_model == '',
            theme=_theme_to_json(body.theme),
            # A theme object that overrides nothing is the clear. It
            # can't use the empty-string trick — it isn't a string —
            # but it lands in the same place: ``_theme_to_json``
            # returns None for it, and NULL is "no branding".
            clear_theme=body.theme is not None and _theme_to_json(body.theme) is None,
        )
        return GroupsService._response(group)

    @staticmethod
    async def delete_group(group_id: str, current_user: PortalUser) -> None:
        """Delete a group. Its people go back to the Default group, in the same transaction."""
        await GroupsService._writable_group(current_user.org_id, group_id)
        await DefaultGroup.ensure_for_org(current_user.org_id, created_by_user_id=current_user.user_id)
        await _db.delete_group(current_user.org_id, group_id)

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    @staticmethod
    async def list_members(group_id: str, org_id: str, by_user_id: str | None = None) -> OrgGroupMemberList:
        """Members of a group in *org_id*, or 404.

        ``org_id`` is not optional and the ``_group_in_org`` call is not
        decoration. This route was the one sibling that took a group id
        with no org — every other member and entry path already went
        through the check — so an admin of any org could read this org's
        membership by knowing the group id, and what came back was
        ``user_id``, ``email``, ``full_name``, who added them and when.

        The docstring on ``_group_in_org`` describes the same bug being
        fixed once before, for the entry endpoints. This is one route
        later.
        """
        await GroupsService._group_in_org(org_id, group_id)
        if by_user_id:
            await DefaultGroup.place_everyone(org_id, by_user_id=by_user_id)
        rows = await _db.list_members(org_id, group_id)
        members = []
        for r in rows:
            user = await _user_db.get_by_user_id(r.user_id)  # type: ignore[arg-type]
            members.append(
                OrgGroupMemberResponse(
                    id=r.id,
                    group_id=r.group_id,
                    user_id=r.user_id,
                    added_by_user_id=r.added_by_user_id,
                    added_at=r.added_at,
                    email=user.email if user else None,
                    full_name=user.full_name if user else None,
                )
            )
        return OrgGroupMemberList(members=members, total=len(members))

    @staticmethod
    async def add_member(
        group_id: str,
        user_id: str,
        current_user: PortalUser,
    ) -> OrgGroupMemberResponse:
        """Put a person in this group — which moves them out of the one they were in.

        Everybody is in exactly one group, so there is no "add" that is
        not a move, and nothing to conflict with: a new hire whose first
        sync already put them in Default is moved from there like
        anybody else. Placing somebody where they already are changes
        nothing. Admin-only, enforced at the endpoint.
        """
        await GroupsService._group_in_org(current_user.org_id, group_id)
        membership = await _team_db.get_active_member(current_user.org_id, user_id)
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='This user is not a member of the organization.',
            )

        member = await _db.place(
            org_id=current_user.org_id,
            group_id=group_id,
            user_id=user_id,
            added_by_user_id=current_user.user_id,
        )
        return OrgGroupMemberResponse(
            id=member.id,
            group_id=member.group_id,
            user_id=member.user_id,
            added_by_user_id=member.added_by_user_id,
            added_at=member.added_at,
            email=membership.invited_email or None,
            full_name=None,
        )

    @staticmethod
    async def remove_member(group_id: str, user_id: str, current_user: PortalUser) -> None:
        """Take a person out of this group, back into the Default one. 409 on Default itself."""
        await GroupsService._group_in_org(current_user.org_id, group_id)
        await DefaultGroup.ensure_for_org(current_user.org_id, created_by_user_id=current_user.user_id)
        await _db.return_to_default(current_user.org_id, group_id, user_id, by_user_id=current_user.user_id)

    @staticmethod
    async def memberships(current_user: PortalUser) -> OrgGroupMembershipList:
        """Every active person of the organisation and the one group they are in.

        What the group page shows next to each person it could move
        here. Everybody not yet placed is put in Default first, so the
        answer has a group for every one of them.
        """
        await DefaultGroup.place_everyone(current_user.org_id, by_user_id=current_user.user_id)
        return OrgGroupMembershipList(
            memberships=[
                OrgGroupMembership(**membership.model_dump())
                for membership in await _db.memberships(current_user.org_id)
            ]
        )

    @staticmethod
    async def get_user_group(user_id: str, org_id: str) -> OrgGroupSummary | None:
        """The group *user_id* belongs to, if that group is in *org_id*.

        Filtered on the way out rather than asked of the database,
        because the question "which group is this user in" is not
        org-scoped at the storage layer and a user could in principle be
        in a group elsewhere. Unfiltered, this answered with another
        org's group id, org_id and name from nothing but a user id —
        enough to enumerate other tenants' group structure.

        Returns None rather than 404 for a user outside the org: the
        route's shape is "no group" and that is the honest answer to give
        somebody who is not entitled to know the difference.
        """
        summary = await _db.get_user_group_summary(user_id)
        if not summary:
            return None
        if str(summary.org_id) != str(org_id):
            return None
        return OrgGroupSummary(**summary.model_dump())

    # ------------------------------------------------------------------
    # Entrys
    # ------------------------------------------------------------------

    @staticmethod
    async def list_entries(group_id: str, org_id: str) -> OrgGroupEntryList:
        await GroupsService._group_in_org(org_id, group_id)
        rows = await _db.list_entries(org_id, group_id)
        entries = [
            OrgGroupEntryResponse(
                id=o.id,
                group_id=o.group_id,
                kind=o.kind,
                entry_name=o.entry_name,
                content=o.content,
                content_type=o.content_type,
                enabled=o.enabled,
                model=o.model,
                source_url=o.source_url,
                source_ref=o.source_ref,
                source_subdir=o.source_subdir,
                created_by_user_id=o.created_by_user_id,
                created_at=o.created_at,
                updated_at=o.updated_at,
            )
            for o in rows
        ]
        return OrgGroupEntryList(entries=entries, total=len(entries))

    @staticmethod
    async def warnings_for(
        kind: str, content: str, *, org_id: str, group_id: str, entry_name: str | None = None
    ) -> list[str]:
        """Anything worth telling the admin about what they just wrote.

        Advisory rather than a refusal — see agent_tools for why a copy
        of igni's tool list must not be allowed to block a name it has
        not heard of yet. The same reasoning covers a hook naming a
        script that is not in the group yet: entries are saved one at a
        time, so refusing would make the order they are created in
        matter.
        """
        if kind == 'agents':
            warnings = []
            # The name first: a tool typo degrades one agent, but a
            # duplicate or placeholder ``name:`` makes the whole entry
            # disappear, so it is the more useful thing to read first.
            # ``entry_name`` is the entry being written, excluded
            # because both callers save first and ask second — so it is
            # already in this list. Without the exclusion every save
            # collided with itself, and a correctly-named agent was told
            # another agent had claimed its name.
            entries = await _db.list_entries(org_id, group_id)
            others = {
                name
                for e in entries
                if str(e.kind) == 'agents'
                and str(e.entry_name) != entry_name
                and (name := agent_frontmatter_name(str(e.content)))
            }
            name_warning = agent_name_warning(content, other_names=others)
            if name_warning:
                warnings.append(name_warning)

            tool_warning = warning_for(content)
            if tool_warning:
                warnings.append(tool_warning)
            return warnings

        if kind == 'hooks':
            entries = await _db.list_entries(org_id, group_id)
            available = {str(e.entry_name) for e in entries if str(e.kind) == 'scripts'}
            script_warning = hook_script_warning(content, available_scripts=available)
            return [script_warning] if script_warning else []

        return []

    @staticmethod
    async def get_entry(group_id: str, entry_id: str, org_id: str) -> OrgGroupEntry:
        """One entry, scoped to the org that owns its group."""
        await GroupsService._group_in_org(org_id, group_id)
        entry = await _db.get_entry(org_id, group_id, entry_id)
        if not entry:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Entry not found')
        return entry

    @staticmethod
    async def create_entry(
        group_id: str,
        body: CreateEntryRequest,
        current_user: PortalUser,
    ) -> OrgGroupEntryResponse:
        await GroupsService._writable_group(current_user.org_id, group_id)

        if body.kind not in VALID_KINDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Invalid kind. Must be one of: {", ".join(sorted(VALID_KINDS))}',
            )
        if body.model and body.kind not in KINDS_WITH_MODEL:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Only agents can name a model; {body.kind!r} cannot.',
            )
        await _refuse_unserved_model(body.model)
        if body.content_type not in VALID_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Invalid content_type. Must be one of: {", ".join(sorted(VALID_CONTENT_TYPES))}',
            )
        expected_content_type = ALLOWED_CONTENT_TYPE.get(body.kind)
        if expected_content_type and body.content_type != expected_content_type:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Kind '{body.kind}' requires content_type "
                    f"'{expected_content_type}' (igni's loader only "
                    f'parses that format).'
                ),
            )

        # Plugin source URL — only meaningful when ``kind == "plugins"``.
        # Validate the scheme at the service layer so an admin who's
        # already past the FE validation (e.g. via curl) still gets
        # a clean 422 instead of a stack trace at install time.
        source_url = body.source_url
        source_ref = body.source_ref
        source_subdir = body.source_subdir
        if source_url is not None:
            parsed = _safe_parse_url(source_url)
            if parsed is None or parsed.scheme not in {'http', 'https', 'git', 'ssh'}:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=('source_url must be an http(s), git, or ssh URL (got a non-URL or unsupported scheme).'),
                )
            # Normalize empty strings to None so the row stores a
            # single "absent" representation regardless of FE shape.
            source_url = source_url.strip() or None
            source_ref = (body.source_ref or '').strip() or None
            source_subdir = (body.source_subdir or '').strip() or None

        entry = await _db.create_entry(
            org_id=current_user.org_id,
            group_id=group_id,
            kind=body.kind,
            entry_name=body.entry_name,
            content=body.content,
            content_type=body.content_type,
            enabled=body.enabled,
            created_by_user_id=current_user.user_id,
            source_url=source_url,
            source_ref=source_ref,
            source_subdir=source_subdir,
            model=body.model or None,
        )
        return OrgGroupEntryResponse(
            id=entry.id,
            group_id=entry.group_id,
            kind=entry.kind,
            entry_name=entry.entry_name,
            content=entry.content,
            content_type=entry.content_type,
            enabled=entry.enabled,
            model=entry.model,
            source_url=entry.source_url,
            source_ref=entry.source_ref,
            source_subdir=entry.source_subdir,
            created_by_user_id=entry.created_by_user_id,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )

    @staticmethod
    async def update_entry(
        group_id: str,
        entry_id: str,
        body: UpdateEntryRequest,
        org_id: str,
    ) -> OrgGroupEntryResponse:
        await GroupsService._writable_group(org_id, group_id)

        if body.model:
            # Same rule as on create, but the kind lives on the row.
            existing = await _db.get_entry(org_id, group_id, entry_id)
            if existing and str(existing.kind) not in KINDS_WITH_MODEL:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f'Only agents can name a model; {existing.kind!r} cannot.',
                )
            await _refuse_unserved_model(body.model)
        entry = await _db.update_entry(
            org_id=org_id,
            group_id=group_id,
            entry_id=entry_id,
            content=body.content,
            enabled=body.enabled,
            model=body.model or None,
            # An empty string clears it; absent leaves it alone.
            clear_model=body.model == '',
        )
        return OrgGroupEntryResponse(
            id=entry.id,
            group_id=entry.group_id,
            kind=entry.kind,
            entry_name=entry.entry_name,
            content=entry.content,
            content_type=entry.content_type,
            enabled=entry.enabled,
            model=entry.model,
            created_by_user_id=entry.created_by_user_id,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )

    @staticmethod
    async def delete_entry(group_id: str, entry_id: str, org_id: str) -> None:
        await GroupsService._writable_group(org_id, group_id)
        await _db.delete_entry(org_id, group_id, entry_id)

    # ------------------------------------------------------------------
    # CLI pack
    # ------------------------------------------------------------------

    @staticmethod
    async def model_for_user(user_id: str) -> str | None:
        """The model this user's group pins, or None.

        Consulted on every chat request that does not name a model, so it
        must be cheap and must never raise: a failure here would turn a
        group lookup into an outage for a request that would otherwise
        have been served perfectly well by the deployment default.
        """
        try:
            # Active membership required (B8), like the pack path: a
            # group's pinned model is the organisation's configuration,
            # and somebody who has been deactivated falls back to the
            # deployment default like any other stranger.
            summary = await _db.get_active_user_group_summary(user_id)
            if not summary:
                return None
            group = await _db.get_group(summary.org_id, summary.id)
            return str(group.default_model) if group and group.default_model else None
        except Exception as exc:  # noqa: BLE001 — routing must survive a group lookup
            logger.warning('Could not resolve a group model for %s: %s', user_id, exc)
            return None

    @staticmethod
    async def get_my_group_pack(user_id: str, org_id: str | None = None) -> OrgGroupPack | None:
        """Return the full pack of entries for the group the user belongs to.

        A user in no group is put in their org's default one, which is
        created and seeded with the shipped agents the first time anyone
        asks. Done here rather than at signup so the orgs that already
        exist are covered too, and here rather than in
        :meth:`model_for_user` because this path runs once per CLI
        start, not once per chat request.

        `org_id` is the organisation the session is in (token claim, else
        header, else first membership — `get_current_account`). A person in
        two organisations gets the pack for the one they are signed into.
        """
        summary = await DefaultGroup.ensure_for_user(user_id, org_id)
        if not summary:
            return None

        group_id = summary.id
        group = await _db.get_group(summary.org_id, group_id)
        if not group:
            return None

        entries = await _db.list_entries(summary.org_id, group_id)
        pack_entries = [
            OrgGroupPackEntry(
                kind=o.kind,
                entry_name=o.entry_name,
                content=o.content,
                content_type=o.content_type,
                enabled=o.enabled,
                model=o.model,
                source_url=o.source_url,
                source_ref=o.source_ref,
                source_subdir=o.source_subdir,
            )
            for o in entries
            if o.enabled
        ]

        return OrgGroupPack(
            group_id=group.id,
            group_name=group.name,
            fetched_at=datetime.now(timezone.utc),
            entries=pack_entries,
            default_model=group.default_model,
            theme=_theme_from_json(group.theme),
            org_id=summary.org_id,
        )


def _theme_to_json(theme: GroupTheme | None) -> str | None:
    """Serialise a theme for the column, or ``None`` if it says nothing.

    A :class:`GroupTheme` with every field unset overrides nothing,
    which *is* igni's palette — so it stores as NULL rather than as
    ``{}``. That keeps one representation of "no branding" in the
    database instead of two that behave identically.
    """
    if theme is None:
        return None
    payload = theme.model_dump(exclude_none=True)
    if not payload:
        return None
    return json.dumps(payload, sort_keys=True)


def _theme_from_json(raw: object) -> GroupTheme | None:
    """Parse a stored theme, treating anything unreadable as unset.

    Validation happened on the way in, so a row that fails here was
    written by an older shape or edited by hand. Refusing to serve the
    group at all would turn a cosmetic problem into an outage for
    everyone in it; falling back to the shipped palette does not.

    Takes ``object`` because every caller hands it a mapped column,
    which is typed ``Column[str]`` and holds a ``str`` at runtime. The
    isinstance check below is what makes that safe rather than assumed,
    and it doubles as the guard for a row holding something other than
    text.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        return GroupTheme.model_validate(json.loads(raw))
    except Exception:
        logger.warning('group theme is unreadable; serving the default palette')
        return None


def _safe_parse_url(value: str):
    """Parse a URL without letting exceptions escape.

    Returns ``None`` for strings that don't parse as URLs — this lets
    the caller distinguish "user typed something URL-ish" from "user
    typed something definitely not a URL".
    """
    try:
        return urlparse(value)
    except (ValueError, AttributeError):
        return None
