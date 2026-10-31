"""Service extensions

A registered service hands igni its extension bundle, signed with its
registered key; igni shows it to a super admin and distributes it to
people's machines. See `docs/service-identity.md`.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'service_extensions',
        sa.Column('client_id', sa.Text(), nullable=False),
        sa.Column('extension_id', sa.Text(), nullable=False),
        sa.Column('version', sa.Text(), nullable=False),
        sa.Column('manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('plugin', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('archive', sa.LargeBinary(), nullable=False),
        sa.Column('sha256', sa.Text(), nullable=False),
        sa.Column('signature', sa.Text(), nullable=False),
        sa.Column('published_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['registered_clients.client_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('client_id'),
    )
    op.create_index('idx_service_extensions_extension_id', 'service_extensions', ['extension_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_service_extensions_extension_id', table_name='service_extensions')
    op.drop_table('service_extensions')
