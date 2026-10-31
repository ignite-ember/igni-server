"""A service still owed the news that an organisation was deleted.

Deleting an organisation in igni must reach every service holding data
for it — and must not depend on every service being up at that moment.
So the deletion itself only writes one row per approved service, in the
same transaction that removes the organisation: the outbox. A
background round (`app.services.identity.organisation_deletions`) calls
each service and deletes the row when it confirms; a service that does
not is tried again later, with backoff, for as long as it stays
registered.

Removing a registration removes what it was owed (`CASCADE`): a service
that is no longer part of the deployment has nobody to tell.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, Index, Integer, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class OrganisationDeletionNotice(Base):
    __tablename__ = 'organisation_deletion_notices'
    __table_args__ = (Index('idx_organisation_deletion_notices_next_attempt_at', 'next_attempt_at'),)

    #: The organisation, which no longer exists: no foreign key.
    org_id: Mapped[str] = Column(Text, primary_key=True)
    client_id: Mapped[str] = Column(
        Text, ForeignKey('registered_clients.client_id', ondelete='CASCADE'), primary_key=True
    )
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    #: Calls made so far that the service did not confirm.
    attempts: Mapped[int] = Column(Integer, nullable=False, default=0)
    #: Not before this. Also the lease: a round that takes a row moves it
    #: forward first, so another replica does not take it meanwhile, and a
    #: replica that died holding it lets it go when the lease runs out.
    next_attempt_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    #: What the last unconfirmed call got back, for an operator.
    last_error: Mapped[str] = Column(Text, nullable=False, default='')
