"""PersonMcpApproval model — a person's approval of one MCP server.

In the cloud, igni in the producer's VM keeps no approvals of its own:
it reads the person's from here. The key is igni's on the desktop
(`~/.igni/mcp-approved.json`): a server's name and the configuration it
came from, so the same name from another file needs approving again.
Group policy still decides which servers are offered at all
(`docs/service-identity.md` §14).
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, func, Index, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase


class PersonMcpApproval(TenantBase):
    __tablename__ = 'person_mcp_approvals'
    __table_args__ = (
        UniqueConstraint('user_id', 'server_name', 'source', name='uq_person_mcp_approvals_user_server_source'),
        Index('idx_person_mcp_approvals_user', 'user_id'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = Column(Text, nullable=False)
    server_name: Mapped[str] = Column(Text, nullable=False)
    #: Where the server's configuration came from — a path in the
    #: workspace, or `group-policy`.
    source: Mapped[str] = Column(Text, nullable=False)
    approved_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )
