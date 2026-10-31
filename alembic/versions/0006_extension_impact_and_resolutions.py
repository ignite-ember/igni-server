"""Extension impact, held versions and collision decisions

An extension's upload records what it changes in igni; a version that
collides with one already distributed is held until a super admin has
decided each collision, and the decisions are kept and distributed. See
`docs/service-identity.md` §8.1.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'service_extensions',
        sa.Column('stage', sa.Text(), server_default=sa.text("'live'"), nullable=False),
    )
    op.add_column(
        'service_extensions',
        sa.Column(
            'impact',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.drop_constraint('service_extensions_pkey', 'service_extensions', type_='primary')
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


def downgrade() -> None:
    op.drop_table('extension_resolutions')
    op.drop_constraint('ck_service_extensions_stage', 'service_extensions', type_='check')
    op.execute("DELETE FROM service_extensions WHERE stage = 'held'")
    op.drop_constraint('service_extensions_pkey', 'service_extensions', type_='primary')
    op.create_primary_key('service_extensions_pkey', 'service_extensions', ['client_id'])
    op.drop_column('service_extensions', 'impact')
    op.drop_column('service_extensions', 'stage')
