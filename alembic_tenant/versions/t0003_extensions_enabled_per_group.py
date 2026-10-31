"""Extensions enabled per group

An approved extension reaches only the groups an org admin has switched
it on for (`docs/service-identity.md` §8.3). A row per (group,
extension); no row is off, which is the default.

Revision ID: t0003
Revises: t0002
Create Date: 2026-10-25

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 't0003'
down_revision: Union[str, Sequence[str], None] = 't0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'org_group_extensions',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('group_id', sa.Text(), nullable=False),
        sa.Column('extension_id', sa.Text(), nullable=False),
        sa.Column('enabled_by_user_id', sa.Text(), nullable=True),
        sa.Column('enabled_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['org_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'extension_id', name='uq_org_group_extensions_group_extension'),
    )
    op.create_index('idx_org_group_extensions_extension', 'org_group_extensions', ['extension_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_org_group_extensions_extension', table_name='org_group_extensions')
    op.drop_table('org_group_extensions')
