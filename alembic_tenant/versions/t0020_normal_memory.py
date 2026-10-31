"""A profile's normal memory size: the VM boots with `memory_mib` and the node's balloon holds it to
`normal_memory_mib` until it needs more; the Standard profile goes to 4 GiB, 1 GiB normally

`docs/service-identity.md` §12.

Revision ID: t0020
Revises: t0019
Create Date: 2026-10-28

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0020'
down_revision: Union[str, Sequence[str], None] = 't0019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('vm_profiles', sa.Column('normal_memory_mib', sa.Integer(), nullable=False, server_default='1024'))
    # A profile smaller than the new default is all normal.
    op.execute('UPDATE vm_profiles SET normal_memory_mib = memory_mib WHERE memory_mib < 1024')
    # The default profile still on the old 1 GiB ceiling may grow to 4 GiB.
    op.execute('UPDATE vm_profiles SET memory_mib = 4096 WHERE is_default AND memory_mib = 1024')


def downgrade() -> None:
    op.drop_column('vm_profiles', 'normal_memory_mib')
