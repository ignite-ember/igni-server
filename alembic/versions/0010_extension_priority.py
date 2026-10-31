"""Collisions between extensions settle by one priority order

A super admin no longer decides collision by collision, and no version
is held back for one: the higher extension in one priority order has
whatever two extensions both want. The decisions and the second stage
of a service's extension go; the order edits applied in becomes that
priority order. See `docs/service-identity.md` §8.1.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-25

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('extension_resolutions')
    # A held upload was the newer one; it is the one that stays.
    op.execute(
        'DELETE FROM service_extensions AS live USING service_extensions AS held '
        "WHERE live.client_id = held.client_id AND live.stage = 'live' AND held.stage = 'held'"
    )
    op.drop_constraint('ck_service_extensions_stage', 'service_extensions', type_='check')
    op.drop_constraint('service_extensions_pkey', 'service_extensions', type_='primary')
    op.drop_column('service_extensions', 'stage')
    op.create_primary_key('service_extensions_pkey', 'service_extensions', ['client_id'])
    op.rename_table('extension_edit_order', 'extension_priority')
    op.execute('ALTER TABLE extension_priority RENAME CONSTRAINT extension_edit_order_pkey TO extension_priority_pkey')
    op.execute(
        'ALTER TABLE extension_priority RENAME CONSTRAINT extension_edit_order_set_by_user_id_fkey '
        'TO extension_priority_set_by_user_id_fkey'
    )


def downgrade() -> None:
    op.execute(
        'ALTER TABLE extension_priority RENAME CONSTRAINT extension_priority_set_by_user_id_fkey '
        'TO extension_edit_order_set_by_user_id_fkey'
    )
    op.execute('ALTER TABLE extension_priority RENAME CONSTRAINT extension_priority_pkey TO extension_edit_order_pkey')
    op.rename_table('extension_priority', 'extension_edit_order')
    op.drop_constraint('service_extensions_pkey', 'service_extensions', type_='primary')
    op.add_column(
        'service_extensions',
        sa.Column('stage', sa.Text(), server_default=sa.text("'live'"), nullable=False),
    )
    op.create_primary_key('service_extensions_pkey', 'service_extensions', ['client_id', 'stage'])
    op.create_check_constraint('ck_service_extensions_stage', 'service_extensions', "stage IN ('live', 'held')")
    op.create_table(
        'extension_resolutions',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('claim', sa.Text(), nullable=False),
        sa.Column('extensions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('winner', sa.Text(), nullable=False),
        sa.Column('decided_by_user_id', sa.Text(), nullable=True),
        sa.Column('break_glass', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('decided_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['decided_by_user_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('claim', 'extensions', name='uq_extension_resolutions_claim_extensions'),
    )
