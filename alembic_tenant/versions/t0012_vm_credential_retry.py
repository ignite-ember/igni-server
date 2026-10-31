"""A VM whose exchange reply was lost may retry once within 60 seconds

`docs/service-identity.md` §16.

Revision ID: t0012
Revises: t0011
Create Date: 2026-10-26

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0012'
down_revision: Union[str, Sequence[str], None] = 't0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('vm_credentials', sa.Column('rotated_at', sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column(
        'vm_credentials', sa.Column('retry_used', sa.Boolean(), server_default=sa.text('false'), nullable=False)
    )


def downgrade() -> None:
    op.drop_column('vm_credentials', 'retry_used')
    op.drop_column('vm_credentials', 'rotated_at')
