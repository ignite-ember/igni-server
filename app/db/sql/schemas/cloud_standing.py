"""What the cloud-standing store hands back: where a person's group runs igni, and who pulls their events."""

from __future__ import annotations

from pydantic import BaseModel

from app.db.sql.models.org_group import GroupMode, LOCAL_AND_CLOUD
from app.db.sql.models.person_event_owner import EventsOwner


class CloudStanding(BaseModel):
    """One person: their group's mode, and the owner they chose, if they ever did."""

    mode: GroupMode = LOCAL_AND_CLOUD
    chosen: EventsOwner | None = None
