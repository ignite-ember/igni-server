"""Shapes the group store returns."""

from __future__ import annotations

from pydantic import BaseModel


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


class GroupMembership(BaseModel):
    """One person's one group in an organisation."""

    user_id: str
    group_id: str
    group_name: str
    is_default: bool
