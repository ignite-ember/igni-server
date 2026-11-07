"""Shapes the group store takes and returns, and what can go wrong.

The errors are domain errors rather than `HTTPException`s, so the
persistence layer does not decide a status code, and a caller that is
not an HTTP request (a startup task, a test) gets something it can catch
by name. The portal turns them back into responses in
`app.services.portal.group_refusals`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self
import uuid

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.exc import IntegrityError

from app.db.sql.models.org_group import OrgGroup


class GroupSummary(BaseModel):
    """Which group somebody is in, and whose org it belongs to.

    Was a three-key dict literal built in two places and read by key in
    two more. The `org_id` is what the callers compare against to
    refuse a cross-tenant read, so it is worth having a name that a
    type checker can follow from one end to the other.
    """

    id: str
    org_id: str
    name: str

    @classmethod
    def of(cls, group: OrgGroup) -> GroupSummary:
        return cls(id=str(group.id), org_id=str(group.org_id), name=str(group.name))


class GroupMembership(BaseModel):
    """One person's one group in an organisation."""

    user_id: str
    group_id: str
    group_name: str
    is_default: bool


class GroupPage(BaseModel):
    """One page of an organisation's groups, and how many there are in all."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[OrgGroup]
    total: int


class GroupTally(BaseModel):
    """How many people and entries one group holds. A group with neither is all zeros."""

    member_count: int = 0
    entry_count: int = 0


class GroupSettings(BaseModel):
    """What a new group is made with."""

    name: str
    description: str | None = None
    default_model: str | None = None
    #: Serialised `GroupTheme`; None is "no branding".
    theme: str | None = None

    def new_group(
        self, *, org_id: str, by_user_id: str | None, is_default: bool, vm_profile_id: str | None, at: datetime
    ) -> OrgGroup:
        """The row for a group made with these settings, created and last updated *at*."""
        return OrgGroup(
            id=str(uuid.uuid4()),
            org_id=org_id,
            name=self.name,
            description=self.description,
            default_model=self.default_model,
            theme=self.theme,
            vm_profile_id=vm_profile_id,
            is_default=is_default,
            created_by_user_id=by_user_id,
            created_at=at,
            updated_at=at,
        )


class GroupPatch(BaseModel):
    """A partial edit to a group. Absent means unchanged.

    `default_model` and `theme` may be set to an explicit None, which
    clears them; `name` and `description` may not, so a None that leaked
    in from an unsupplied request field cannot wipe them. Built one field
    at a time by assignment, which counts as supplying it.
    """

    model_config = ConfigDict(validate_assignment=True)

    name: str | None = None
    description: str | None = None
    default_model: str | None = None
    theme: str | None = None

    @model_validator(mode='after')
    def _name_and_description_are_never_cleared(self) -> Self:
        for field in ('name', 'description'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} can be changed but not cleared')
        return self

    def apply_to(self, row: OrgGroup, *, at: datetime) -> None:
        """Write the fields this patch supplies onto *row*, and mark it updated *at*.

        `name` and `description` are applied when not None (they can never
        be cleared); `default_model` and `theme` whenever supplied, so an
        explicit None clears them.
        """
        if self.name is not None:
            row.name = self.name
        if self.description is not None:
            row.description = self.description
        if 'default_model' in self.model_fields_set:
            row.default_model = self.default_model
        if 'theme' in self.model_fields_set:
            row.theme = self.theme
        row.updated_at = at


class EntryCreate(BaseModel):
    """What a new entry is made with."""

    kind: str
    entry_name: str
    content: str
    content_type: str
    enabled: bool
    source_url: str | None = None
    source_ref: str | None = None
    source_subdir: str | None = None
    model: str | None = None


class EntryPatch(BaseModel):
    """A partial edit to an entry. Absent means unchanged; an explicit None `model` clears it."""

    model_config = ConfigDict(validate_assignment=True)

    content: str | None = None
    enabled: bool | None = None
    model: str | None = None

    @model_validator(mode='after')
    def _content_and_enabled_are_never_cleared(self) -> Self:
        for field in ('content', 'enabled'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} can be changed but not cleared')
        return self


# ----------------------------------------------------------------------
# What can go wrong
# ----------------------------------------------------------------------


class GroupNotFound(LookupError):
    """No group with that id in this organisation."""

    def __init__(self, message: str = 'Group not found') -> None:
        super().__init__(message)


class TemplateGroupNotFound(GroupNotFound):
    """The group a copy was asked to start from is not in this organisation."""

    def __init__(self) -> None:
        super().__init__('The group to copy from does not exist in this organization.')


class MemberNotFound(LookupError):
    """That person is not in that group."""

    def __init__(self) -> None:
        super().__init__('Member not found')


class EntryNotFound(LookupError):
    """No entry with that id in that group."""

    def __init__(self) -> None:
        super().__init__('Entry not found')


class GroupConflict(Exception):
    """The change contradicts what is already stored."""


class GroupNameTaken(GroupConflict):
    """Another group in this organisation already has that name.

    The race backstop for the service's own pre-check. That pre-check is
    a check-then-act, so two concurrent creates both pass it: 8 at once
    gave one 201, three 409s from the pre-check, and four 500s before the
    constraint was caught. The message matches the pre-check's, so a
    caller cannot tell which one answered.

    Recognised by the constraint's name, not by the word "name" in the
    driver's message: a NOT NULL violation on this very column says
    `null value in column "name" ...`, and reading that as "a group named
    X already exists" would be both the wrong status and a sentence that
    sends somebody looking for a group that is not there.
    """

    constraint = 'uq_org_groups_org_name'

    def __init__(self, name: str) -> None:
        super().__init__(f"A group named '{name}' already exists in this organization.")
        self.name = name

    @classmethod
    def raised_by(cls, exc: IntegrityError) -> bool:
        return cls.constraint in str(exc.orig)


class EntryNameTaken(GroupConflict):
    """The group already has an entry of that kind with that name.

    Recognised by the constraint's name for the same reason as
    `GroupNameTaken`: a foreign-key violation from a group deleted
    meanwhile is not "already exists", and is left to surface as itself.
    """

    constraint = 'uq_org_group_entries_entry'

    def __init__(self, *, kind: str, entry_name: str) -> None:
        super().__init__(f"An entry named '{entry_name}' of kind '{kind}' already exists in this group.")
        self.kind = kind
        self.entry_name = entry_name

    @classmethod
    def raised_by(cls, exc: IntegrityError) -> bool:
        return cls.constraint in str(exc.orig)


class DefaultGroupKeepsItsPeople(GroupConflict):
    """Somebody was asked to leave the Default group, which has nowhere further back to put them."""

    def __init__(self) -> None:
        super().__init__('Nobody leaves the Default group except by being placed in another group.')


class NoDefaultGroup(RuntimeError):
    """The organisation has no Default group.

    Callers make sure it exists first (`DefaultGroup.ensure_for_org`), so
    reaching this is a bug rather than a state to answer for: it is left
    to surface as a 500.
    """

    def __init__(self, org_id: str) -> None:
        super().__init__(f'Organisation {org_id} has no Default group yet.')
        self.org_id = org_id
