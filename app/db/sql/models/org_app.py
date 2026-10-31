"""OrgApp model — an app, the organisation's other kind of producer.

An app is registered by an org admin: its name and keys are a registered
client of kind `app` on the shared plane (`registered_clients`, whose
`client_id` is `app_id` here); what is the organisation's own about it
lives here. An app is in no group and acts only as itself, never on
behalf of a person (`docs/service-identity.md` §11).
"""

from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, func, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase


class OrgApp(TenantBase):
    __tablename__ = 'org_apps'

    #: The registered client's id — a different database, so no foreign key.
    app_id: Mapped[str] = Column(Text, primary_key=True)
    #: The member answerable for the app. Informational: the org's admins
    #: manage it. Nullable, never a foreign key, so it outlives them.
    owner_user_id: Mapped[str | None] = Column(Text, nullable=True)
    #: The VM profile its producers' microVMs get in the cloud; null is
    #: the organisation's default profile.
    vm_profile_id: Mapped[str | None] = Column(Text, ForeignKey('vm_profiles.id', ondelete='SET NULL'), nullable=True)
    created_by_user_id: Mapped[str | None] = Column(Text, nullable=True)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
