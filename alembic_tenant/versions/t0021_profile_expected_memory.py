"""A profile's expected memory (for placement) instead of dropping caches before suspend

`drop_caches_before_suspend` is gone: nothing used it, and closing heavy caches before a suspend only
makes the next resume read them again. `expected_memory_mib` (null: the node's rule) is what the node
expects the VM to use when it places it. `docs/service-identity.md` §12.

Revision ID: t0021
Revises: t0020
Create Date: 2026-10-28

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0021'
down_revision: Union[str, Sequence[str], None] = 't0020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('vm_profiles', 'drop_caches_before_suspend')
    op.add_column('vm_profiles', sa.Column('expected_memory_mib', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('vm_profiles', 'expected_memory_mib')
    op.add_column(
        'vm_profiles',
        sa.Column('drop_caches_before_suspend', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
