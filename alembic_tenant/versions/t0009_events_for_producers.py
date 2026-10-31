"""Events services ask igni to deliver to their extension for a producer

`docs/service-identity.md` §15. Stored until delivered; `expires_at` is
the service's, bounded by the platform's setting when it has one.

Revision ID: t0009
Revises: t0008
Create Date: 2026-10-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 't0009'
down_revision: Union[str, Sequence[str], None] = 't0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'producer_events',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('client_id', sa.Text(), nullable=False),
        sa.Column('extension_id', sa.Text(), nullable=False),
        sa.Column('producer_kind', sa.Text(), nullable=False),
        sa.Column('producer_id', sa.Text(), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('event_key', sa.Text(), nullable=True),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('handed_out_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('delivered_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('pending', 'delivered', 'dropped')", name='ck_producer_events_state'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_producer_events_pending', 'producer_events', ['producer_kind', 'producer_id', 'state'], unique=False
    )
    op.create_index(
        'uq_producer_events_key',
        'producer_events',
        ['extension_id', 'producer_kind', 'producer_id', 'event_key'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_producer_events_key', table_name='producer_events')
    op.drop_index('idx_producer_events_pending', table_name='producer_events')
    op.drop_table('producer_events')
