"""The order edits from several extensions apply in

Several extensions' prompt edits stack; a super admin sets the order
once, and every machine receives it with the distribution listing. See
`docs/service-identity.md` §8.1.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-25

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'extension_edit_order',
        sa.Column('extension_id', sa.Text(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('set_by_user_id', sa.Text(), nullable=True),
        sa.Column('break_glass', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('set_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['set_by_user_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('extension_id'),
    )


def downgrade() -> None:
    op.drop_table('extension_edit_order')
