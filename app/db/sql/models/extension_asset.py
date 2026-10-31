"""A file a service's extension runs that is not Python: a JVM, a database, a model.

Declared in the extension's manifest with its size and SHA-256 — the
manifest is inside the signed bundle, so the signature covers it — and
uploaded beside the bundle, once per digest. A new version that keeps
its Neo4j does not upload it again, and a machine downloads only the
assets for its own platform. See `docs/service-identity.md` §8.2.

Kept per client: an asset belongs to the service that uploaded it, and
goes when no version that service has uploaded still declares it.

The bytes are a Postgres large object, not a column: a JRE or a model is
hundreds of megabytes, and a `bytea` value is read and written whole. A
large object is written and read in chunks, so neither an upload nor a
download ever holds the file in memory. A trigger unlinks the object
whenever its row goes, however it goes — the cleanup below, or the
cascade when the service's client is removed (migration 0009).
"""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, ForeignKey, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import OID
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class ExtensionAsset(Base):
    __tablename__ = 'extension_assets'

    client_id: Mapped[str] = Column(
        Text, ForeignKey('registered_clients.client_id', ondelete='CASCADE'), primary_key=True
    )
    #: Hex SHA-256 of the bytes, which is also its name.
    sha256: Mapped[str] = Column(Text, primary_key=True)
    size: Mapped[int] = Column(BigInteger, nullable=False)
    #: The large object holding the bytes.
    content_oid: Mapped[int] = Column(OID, nullable=False)
    #: The compact JWS by the client's registered key over `{"asset", "size"}`.
    signature: Mapped[str] = Column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
