"""Pydantic schemas for portal group management.

A group's theme lives in `group_theme`, and its entries in `group_entries`.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from app.api.base_v1.schema.portal.group_entries import OrgGroupPackEntry
from app.api.base_v1.schema.portal.group_theme import GroupTheme
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_group_member import OrgGroupMember
from app.db.sql.schemas.group import GroupPatch

# ------------------------------------------------------------------
# Groups
# ------------------------------------------------------------------


class OrgGroupResponse(BaseModel):
    """A single org group."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    name: str
    description: str | None = None
    # Which model this group's people get when they do not name one.
    # Usually a LoRA adapter — see ``OrgGroup.default_model``.
    default_model: str | None = None
    # Brand overrides. Null is igni's palette. Parsed from the column's
    # JSON by the service, so the portal edits a typed object rather
    # than a string it has to parse itself.
    theme: GroupTheme | None = None
    # The group seeded with what igni ships. Shown as such when picking
    # a template, since "the standard set" is the one people want.
    is_default: bool = False
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, group: OrgGroup) -> Self:
        """A stored group as the portal sees it, its theme parsed."""
        return cls(
            id=group.id,
            org_id=group.org_id,
            name=group.name,
            description=group.description,
            default_model=group.default_model,
            theme=GroupTheme.from_column(group.theme),
            is_default=bool(group.is_default),
            created_by_user_id=group.created_by_user_id,
            created_at=group.created_at,
            updated_at=group.updated_at,
        )


class OrgGroupDetail(OrgGroupResponse):
    """Group with member and entry counts."""

    member_count: int = 0
    entry_count: int = 0

    @classmethod
    def of(cls, group: OrgGroup, *, member_count: int = 0, entry_count: int = 0) -> Self:
        """A stored group with how many people and entries it holds."""
        return super().of(group).model_copy(update={'member_count': member_count, 'entry_count': entry_count})


class OrgGroupList(BaseModel):
    """List of org groups."""

    groups: list[OrgGroupDetail]
    total: int
    offset: int = 0
    limit: int | None = None


class CreateGroupRequest(BaseModel):
    """Request to create a new group."""

    name: str = Field(..., min_length=1, max_length=120, description='Group name (unique within org)')
    description: str | None = Field(None, description='Optional description')
    default_model: str | None = Field(
        None, description='Model or LoRA adapter this group routes to when the caller names none'
    )
    template_group_id: str | None = Field(
        None,
        description=(
            'Copy every entry and extension from this group. Any group can be a template, including the '
            'Default one that holds what igni ships. Omit to start empty. Nobody is copied.'
        ),
    )
    theme: GroupTheme | None = Field(None, description="Brand overrides. Omit for igni's palette.")


class CopyGroupRequest(BaseModel):
    """Copy a group: its entries, extensions and settings, under a new name, with nobody in it."""

    name: str = Field(..., min_length=1, max_length=120, description='Name of the copy (unique within org)')
    description: str | None = Field(None, description="The copy's description. Omit to keep the original's.")


class UpdateGroupRequest(BaseModel):
    """Request to update a group.

    ``default_model`` follows the usual partial-update rule — absent
    leaves it alone — with an empty string as the explicit way to clear
    it, since ``None`` already means "not supplied".

    ``theme`` needs the same distinction and cannot borrow the same
    trick, being an object rather than a string: an empty
    :class:`GroupTheme` (every field null) is the explicit "clear it",
    and absent still means "leave it alone". Both end up storing NULL —
    a theme that overrides nothing *is* igni's palette — so the two
    spellings agree on the result.
    """

    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    default_model: str | None = None
    theme: GroupTheme | None = None

    def to_patch(self) -> GroupPatch:
        """The edit this request asks for: only the fields it supplies.

        The one place a `GroupPatch` is built from a request, because a
        field set to None on it is a clear, and a None standing for "not
        in the request" would wipe what was stored. An empty
        `default_model` is the explicit clear. A theme that overrides
        nothing is the theme's clear: `GroupTheme.to_column` returns None
        for it, and NULL is "no branding".
        """
        patch = GroupPatch()
        if self.name is not None:
            patch.name = self.name
        if self.description is not None:
            patch.description = self.description
        if self.default_model is not None:
            patch.default_model = self.default_model or None
        if self.theme is not None:
            patch.theme = self.theme.to_column()
        return patch


# ------------------------------------------------------------------
# Members
# ------------------------------------------------------------------


class OrgGroupMemberResponse(BaseModel):
    """A user assigned to a group."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    user_id: str
    added_by_user_id: str
    added_at: datetime
    # Resolved from users table
    email: str | None = None
    full_name: str | None = None

    @classmethod
    def of(cls, member: OrgGroupMember, *, email: str | None, full_name: str | None) -> OrgGroupMemberResponse:
        """A stored placement, with who the person is."""
        return cls(
            id=member.id,
            group_id=member.group_id,
            user_id=member.user_id,
            added_by_user_id=member.added_by_user_id,
            added_at=member.added_at,
            email=email,
            full_name=full_name,
        )


class OrgGroupMemberList(BaseModel):
    """List of group members."""

    members: list[OrgGroupMemberResponse]
    total: int


class AddMemberRequest(BaseModel):
    """Put a person in this group.

    A person is in exactly one group, so this moves them out of the one
    they are in — the Default group, for anybody nobody has placed yet.
    """

    user_id: str = Field(..., description='User ID to move into this group')


class OrgGroupMembership(BaseModel):
    """Which one group a person of the organisation is in."""

    user_id: str
    group_id: str
    group_name: str
    is_default: bool


class OrgGroupMembershipList(BaseModel):
    """Every person of the organisation with their group."""

    memberships: list[OrgGroupMembership]


# ------------------------------------------------------------------
# CLI pack (consumed by igni CLI)
# ------------------------------------------------------------------


class OrgGroupSummary(BaseModel):
    """Minimal group info for the CLI to know which group it's in."""

    id: str
    org_id: str
    name: str


class OrgGroupPack(BaseModel):
    """Full pack of entries for a group — consumed by igni CLI."""

    group_id: str
    group_name: str
    fetched_at: datetime
    entries: list[OrgGroupPackEntry]
    # What the group's people get when an agent names no model.
    default_model: str | None = None
    # Brand overrides, applied by the client's UI. Group-level like
    # ``default_model`` rather than an entry, and delivered the same
    # way — this pack is already polled with an ETag, so branding costs
    # no extra request and an unchanged theme costs no bytes.
    theme: GroupTheme | None = None
    #: The organisation the group belongs to. Not part of the served
    #: payload (excluded from `as_payload`, so the response and its ETag
    #: are what they were); carried so the signed form can name it.
    org_id: str | None = Field(default=None, exclude=True)

    def as_payload(self) -> dict:
        """The pack as JSON-ready types.

        `mode='json'`, so `fetched_at` leaves as an ISO string. Python
        mode hands `JSONResponse`'s encoder a raw `datetime`, which it
        refuses — and that is a property of this model's fields, so it
        belongs here rather than in a comment beside one caller.
        """
        return self.model_dump(mode='json')

    def etag(self, *, bound_to: str = '') -> str:
        """A validator for "has this pack changed".

        **`fetched_at` is excluded, and that is the whole point.** It
        is `now()`, so a tag over the whole payload differs on every
        request: present, well-formed, and unable to match by
        construction. The tag covers what identifies the pack, not
        when it was read.

        `usedforsecurity=False` is not only documentation. This is a
        cache validator and not a signature, and on a FIPS-enabled
        host a bare `hashlib.md5()` raises — which would make this a
        500 on exactly the kind of regulated deployment a self-hosted
        product is installed into.

        `bound_to` folds in what the signed form depends on besides the
        content: who it was signed for and with which key (see
        `GroupPackSigner.binding`). Without it, somebody else signing in
        on the same machine, or the first poll after a key rotation,
        would get a 304 and keep a signature that no longer verifies.
        """
        identity: dict = {key: value for key, value in self.as_payload().items() if key != 'fetched_at'}
        if bound_to:
            identity['_bound_to'] = bound_to
        digest = hashlib.md5(json.dumps(identity, sort_keys=True).encode(), usedforsecurity=False)
        return f'"{digest.hexdigest()}"'


class SignedOrgGroupPack(OrgGroupPack):
    """What `GET /v1/portal/me/group/pack` serves: the pack, and the pack signed.

    Every field of `OrgGroupPack` is still here, unchanged, for clients
    that read them. `signed_pack` is the same content as a compact JWS
    (`app.services.portal.group_pack_signature`); igni applies only that.
    """

    signed_pack: str


class GroupExtension(BaseModel):
    """One approved extension, and whether this group's people get it."""

    extension_id: str
    name: str
    description: str
    #: The service it comes from — where its website, and its admin
    #: screens, are.
    service_url: str
    enabled: bool


class GroupExtensionList(BaseModel):
    """Every approved extension, each with whether this group has it on.

    Off by default: a newly approved extension reaches nobody until an
    org admin switches it on for a group (`docs/service-identity.md` §8.3).
    """

    extensions: list[GroupExtension]


class SetGroupExtension(BaseModel):
    enabled: bool
