"""The latest time this deployment has seen, for the licence (`app.services.licence.clock`).

One row (`id` = 1). Only ever raised, and only while this process's
clock and the database's agree, so setting the clock back does not set
the licence back and a clock set wrongly into the future does not lapse
it for good (docs/licence-file.md, "The clock").
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Column, Integer, text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class LicenceClock(Base):
    __tablename__ = 'licence_clock'
    __table_args__ = (CheckConstraint('id = 1', name='ck_licence_clock_one_row'),)

    id: Mapped[int] = Column(Integer, primary_key=True, default=1)
    seen: Mapped[datetime] = Column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('now()'))
