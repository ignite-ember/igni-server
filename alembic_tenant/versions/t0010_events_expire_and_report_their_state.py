"""Events expire, and their service reads what became of each

An undelivered event expires after at most 14 days
(`SERVICE_EVENT_MAX_WAIT_SECONDS`); `expired` joins the states, a drop
says why, and `updated_at` is the cursor a service polls its events'
states with (`docs/service-identity.md` §15).

Revision ID: t0010
Revises: t0009
Create Date: 2026-10-26

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0010'
down_revision: Union[str, Sequence[str], None] = 't0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('ck_producer_events_state', 'producer_events', type_='check')
    op.create_check_constraint(
        'ck_producer_events_state', 'producer_events', "state IN ('pending', 'delivered', 'expired', 'dropped')"
    )
    op.add_column('producer_events', sa.Column('dropped_reason', sa.Text(), nullable=True))
    op.add_column(
        'producer_events',
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('idx_producer_events_client', 'producer_events', ['client_id', 'updated_at'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_producer_events_client', table_name='producer_events')
    op.drop_column('producer_events', 'updated_at')
    op.drop_column('producer_events', 'dropped_reason')
    op.execute("UPDATE producer_events SET state = 'dropped' WHERE state = 'expired'")
    op.drop_constraint('ck_producer_events_state', 'producer_events', type_='check')
    op.create_check_constraint(
        'ck_producer_events_state', 'producer_events', "state IN ('pending', 'delivered', 'dropped')"
    )
