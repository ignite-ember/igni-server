"""OrgGroup model — an org-scoped user group."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, ForeignKey, func, Index, Text, text, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase


class OrgGroup(TenantBase):
    __tablename__ = 'org_groups'
    __table_args__ = (
        Index('idx_org_groups_org', 'org_id'),
        # A constraint, not a unique index. Both enforce uniqueness, but
        # the migrations build a constraint, and a model that describes
        # the same rule with a different object makes every future
        # ``--autogenerate`` propose swapping one for the other.
        UniqueConstraint('org_id', 'name', name='uq_org_groups_org_name'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id: Mapped[str] = Column(Text, nullable=False)
    name: Mapped[str] = Column(Text, nullable=False)
    description: Mapped[str | None] = Column(Text, nullable=True)
    # Which model this group's people get when they do not name one.
    #
    # Usually a LoRA adapter: a legal team pinned to a legal-review
    # adapter, engineering to another, both served by the same GPUs. Null
    # means the deployment's own default, which is what the chosen node
    # advertises first.
    default_model: Mapped[str | None] = Column(Text, nullable=True)

    # Brand overrides for this group's people: accent colours and a
    # brand mark, as JSON validated by ``GroupTheme`` on the way in.
    # Null is igni's own palette, which is every row that predates this.
    #
    # A column rather than a ``kind='settings'`` entry. Entries are
    # named files — the client materialises them under
    # ``group-policy/<kind>/<name>`` for agents to read — and a theme is
    # neither a file nor agent input. It is group-level UI config, the
    # same shape as ``default_model`` above, and it rides the pack the
    # same way.
    theme: Mapped[str | None] = Column(Text, nullable=True)

    #: The VM profile its producers' microVMs get in the cloud; null is
    #: the organisation's default profile.
    vm_profile_id: Mapped[str | None] = Column(Text, ForeignKey('vm_profiles.id', ondelete='SET NULL'), nullable=True)
    # The group seeded with what igni ships, one per org. A flag
    # rather than a name match: an admin who renames it has adopted it,
    # and matching on 'Default' would hand them a fresh one the next
    # time anything asked.
    is_default: Mapped[bool] = Column(Boolean, nullable=False, server_default=text('false'))

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
