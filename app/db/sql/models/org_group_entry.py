"""OrgGroupEntry model — an agent/MCP/plugin/settings entry entry for a group."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, ForeignKey, func, Index, Text, text, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase


class OrgGroupEntry(TenantBase):
    __tablename__ = 'org_group_entries'
    __table_args__ = (
        Index('idx_org_group_entries_group', 'group_id'),
        Index('idx_org_group_entries_kind', 'group_id', 'kind'),
        UniqueConstraint('group_id', 'kind', 'entry_name', name='uq_org_group_entries_entry'),
        # Partial: only plugin entries carry a source, and indexing the
        # nulls would be most of the table for nothing.
        Index(
            'idx_org_group_entries_source_url',
            'source_url',
            postgresql_where=text('source_url IS NOT NULL'),
        ),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id: Mapped[str] = Column(Text, ForeignKey('org_groups.id', ondelete='CASCADE'), nullable=False)
    kind: Mapped[str] = Column(Text, nullable=False)  # agents | mcps | plugins | settings
    entry_name: Mapped[str] = Column(Text, nullable=False)
    content: Mapped[str] = Column(Text, nullable=False)
    content_type: Mapped[str] = Column(Text, nullable=False)  # markdown | yaml | json
    enabled: Mapped[bool] = Column(Boolean, nullable=False, server_default=text('true'))
    # Plugin install source trio. All nullable so non-plugin kinds are
    # unaffected. The partial index on ``source_url`` is in
    # ``__table_args__`` above.
    source_url: Mapped[str | None] = Column(Text, nullable=True)
    source_ref: Mapped[str | None] = Column(Text, nullable=True)
    source_subdir: Mapped[str | None] = Column(Text, nullable=True)
    # Which model this entry runs against. Only meaningful for
    # ``kind == 'agents'``: a legal group might review contracts on one
    # adapter and summarise cases on another. Null falls back to the
    # group's default, then to the deployment's.
    model: Mapped[str | None] = Column(Text, nullable=True)

    # ``SET NULL`` and nullable: this records who made the row, and the
    # row has to outlive them. With ``NO ACTION`` and ``NOT NULL``,
    # deleting an account raised a foreign-key violation the moment its
    # owner had ever created anything — a 500 on "delete my account" for
    # anybody who had done admin work and then left an org that survives.
    # CASCADE would have been worse: an admin closing their account would
    # take the organisation's policy with them.
    created_by_user_id: Mapped[str | None] = Column(Text, nullable=True)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
