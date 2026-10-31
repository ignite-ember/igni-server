"""The producers feed carries each producer's VM profile, not only its version

The gateway keeps the profile with the producer and wakes its VM from
it, so a wake never asks igni server (`docs/service-identity.md` §11.3,
§12). Every producer is marked, so the next read of the feed writes its
profile and lists it again.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-31

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0026'
down_revision: Union[str, Sequence[str], None] = '0025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('producer_feed', sa.Column('vm_profile', postgresql.JSONB(), nullable=True))
    op.execute("SELECT producer_feed_mark('*', '*', '*')")


def downgrade() -> None:
    op.drop_column('producer_feed', 'vm_profile')
