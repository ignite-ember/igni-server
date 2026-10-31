"""Each life of a producer is a row of its own in the feed

A person removed from an organisation is removed there — their VM, its
volume and keys erased — and if added again is a new incarnation (their
new membership's id) with a new VM; the removed row is never brought
back. An app's or a space's incarnation is its own id. See
`docs/service-identity.md` §11.3.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0018'
down_revision: Union[str, Sequence[str], None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('producer_feed', sa.Column('incarnation', sa.Text(), nullable=True))
    op.execute('UPDATE producer_feed SET incarnation = producer_id')
    op.alter_column('producer_feed', 'incarnation', nullable=False)
    op.drop_constraint('pk_producer_feed', 'producer_feed', type_='primary')
    op.create_primary_key(
        'pk_producer_feed', 'producer_feed', ['org_id', 'producer_kind', 'producer_id', 'incarnation']
    )


def downgrade() -> None:
    op.drop_constraint('pk_producer_feed', 'producer_feed', type_='primary')
    op.create_primary_key('pk_producer_feed', 'producer_feed', ['org_id', 'producer_kind', 'producer_id'])
    op.drop_column('producer_feed', 'incarnation')
