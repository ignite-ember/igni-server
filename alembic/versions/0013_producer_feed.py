"""The gateway's feed of new and changed producers

One row per producer with the state last reported, renumbered from a
sequence whenever it changes. See `docs/service-identity.md` §11.3.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0013'
down_revision: Union[str, Sequence[str], None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence('producer_feed_seq')))
    op.create_table(
        'producer_feed',
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('producer_kind', sa.Text(), nullable=False),
        sa.Column('producer_id', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('profile_version', sa.Text(), nullable=False),
        sa.Column('packages_version', sa.Text(), nullable=False),
        sa.Column('changed_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('seq', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('org_id', 'producer_kind', 'producer_id', name='pk_producer_feed'),
    )
    op.create_index('uq_producer_feed_seq', 'producer_feed', ['seq'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_producer_feed_seq', table_name='producer_feed')
    op.drop_table('producer_feed')
    op.execute(sa.schema.DropSequence(sa.Sequence('producer_feed_seq')))
