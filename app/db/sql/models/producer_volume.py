"""ProducerVolumeSize model — one producer's volume, grown by an admin past its profile's size.

At most the profile's `volume_max_gib` when grown; never shrunk. It is
for one incarnation of the producer (`docs/service-identity.md` §11.3):
a person added to the organisation again starts at the profile's size.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, PrimaryKeyConstraint, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase


class ProducerVolumeSize(TenantBase):
    __tablename__ = 'producer_volumes'
    __table_args__ = (PrimaryKeyConstraint('producer_kind', 'producer_id', name='pk_producer_volumes'),)

    producer_kind: Mapped[str] = Column(Text, nullable=False)
    producer_id: Mapped[str] = Column(Text, nullable=False)
    #: A person's membership id, an app's or a space's own id.
    incarnation: Mapped[str] = Column(Text, nullable=False)
    gib: Mapped[int] = Column(Integer, nullable=False)
    #: Who grew it: `person`, and their id; `acting_as` org_admin or super_admin.
    grown_by_kind: Mapped[str] = Column(Text, nullable=False)
    grown_by_id: Mapped[str] = Column(Text, nullable=False)
    grown_as: Mapped[str] = Column(Text, nullable=False)
    grown_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
