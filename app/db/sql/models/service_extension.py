"""The extension a registered service ships, as the service handed it over.

Extensions reach people's machines only from their service: the service
signs its bundle with the key it registered and uploads it, igni keeps
it here, shows a super admin what it contains, and hands it to every
signed-in machine once the service is approved. So the extension on a
laptop is always the one built for the service that is deployed.

At most two per client, by `stage`. `live` is what is distributed once
the service is approved: a pending service's latest upload, and an
approved service's latest upload that asks for no more than the live one
did. `held` is an approved service's upload that asks for more
(`app.services.identity.grants_comparison`): it waits there, while `live` keeps
reaching machines, until a super admin approves it — then it becomes
`live` — or rejects it. A newer upload replaces a held one. A collision
with another extension never holds anything back — the super admin's
priority order settles it (`app.services.identity.impact`).

`manifest`, `plugin` and `impact` are what the portal shows — read out of
the archive at upload, so the page cannot describe a bundle other than
the one that is distributed.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, LargeBinary, Text, text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base

#: Distributed, once the service is approved.
LIVE = 'live'
#: Uploaded by an approved service, asking for more than `live`; waiting
#: for a super admin.
HELD = 'held'


class ServiceExtension(Base):
    __tablename__ = 'service_extensions'
    __table_args__ = (
        Index('idx_service_extensions_extension_id', 'extension_id'),
        CheckConstraint("stage IN ('live', 'held')", name='ck_service_extensions_stage'),
    )

    client_id: Mapped[str] = Column(
        Text, ForeignKey('registered_clients.client_id', ondelete='CASCADE'), primary_key=True
    )
    #: `live` or `held`.
    stage: Mapped[str] = Column(Text, primary_key=True, server_default=text("'live'"), default=LIVE)
    #: The manifest's `id`, e.g. `igni.artifacts`.
    extension_id: Mapped[str] = Column(Text, nullable=False)
    version: Mapped[str] = Column(Text, nullable=False)
    manifest: Mapped[dict[str, object]] = Column(JSONB, nullable=False)
    #: The plugin's agents and skills, summarised. See `ExtensionBundle`.
    plugin: Mapped[dict[str, object]] = Column(JSONB, nullable=False)
    #: What it changes in igni. See `app.services.identity.impact.ExtensionImpact`.
    impact: Mapped[dict[str, object]] = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    #: The bundle, a gzipped tar with `manifest.json` at its root.
    archive: Mapped[bytes] = Column(LargeBinary, nullable=False)
    #: Hex SHA-256 of `archive`; what `signature` covers.
    sha256: Mapped[str] = Column(Text, nullable=False)
    #: A compact JWS by the client's registered key over
    #: `{"extension", "version", "sha256"}`.
    signature: Mapped[str] = Column(Text, nullable=False)
    published_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
