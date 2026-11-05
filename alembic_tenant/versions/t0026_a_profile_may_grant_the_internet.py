"""A VM profile may grant its VMs the internet

`internet` (default false): a VM with the profile may reach the internet;
otherwise only what its node allows (`[[network.allow]]`). Internet access
is granted per profile, not per node: a node's `egress = "internet"` is
only its capability to grant it, and an air-gapped node never does. Every
existing profile stays internal-only. `docs/service-identity.md` §12.4.

Revision ID: t0026
Revises: t0025
Create Date: 2026-11-03

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0026'
down_revision: Union[str, Sequence[str], None] = 't0025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('vm_profiles', sa.Column('internet', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    op.drop_column('vm_profiles', 'internet')
