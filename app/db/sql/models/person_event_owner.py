"""PersonEventOwner model — which side pulls a person's service events: their desktop or their cloud VM.

Both used to lease from the same queue, so whichever asked first had an
event for two minutes, and the side without the extension acknowledged
it as not handled. igni server now serves one owner per person
(igni-core `docs/design/local-cloud-switch.md` §7). The group's mode
decides where it can; this row is the person's own choice, read only
when their group runs igni in both places.

Kept apart from `org_group_members`, so moving somebody to another group
does not reset where their events go.
"""

from datetime import datetime, timezone
from typing import Final, Literal

from sqlalchemy import CheckConstraint, Column, func, PrimaryKeyConstraint, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase

#: igni on the person's own computer pulls `/v1/me/events`.
LOCAL_OWNER: Final = 'local'
#: The gateway pulls them for the person's cloud VM.
CLOUD_OWNER: Final = 'cloud'

EventsOwner = Literal['local', 'cloud']


class PersonEventOwner(TenantBase):
    __tablename__ = 'person_event_owner'
    __table_args__ = (
        PrimaryKeyConstraint('org_id', 'user_id', name='pk_person_event_owner'),
        CheckConstraint("owner IN ('local', 'cloud')", name='ck_person_event_owner_owner'),
    )

    org_id: Mapped[str] = Column(Text, nullable=False)
    user_id: Mapped[str] = Column(Text, nullable=False)
    owner: Mapped[EventsOwner] = Column(Text, nullable=False)
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )
