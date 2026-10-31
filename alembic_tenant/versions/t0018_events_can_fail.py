"""An event the gateway gave up delivering is failed, with the reason

`docs/service-identity.md` §15.

Revision ID: t0018
Revises: t0017
Create Date: 2026-10-28

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0018'
down_revision: Union[str, Sequence[str], None] = 't0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('ck_producer_events_state', 'producer_events', type_='check')
    op.create_check_constraint(
        'ck_producer_events_state',
        'producer_events',
        "state IN ('pending', 'delivered', 'expired', 'dropped', 'failed')",
    )
    op.add_column('producer_events', sa.Column('failed_reason', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('producer_events', 'failed_reason')
    op.execute("UPDATE producer_events SET state = 'dropped' WHERE state = 'failed'")
    op.drop_constraint('ck_producer_events_state', 'producer_events', type_='check')
    op.create_check_constraint(
        'ck_producer_events_state', 'producer_events', "state IN ('pending', 'delivered', 'expired', 'dropped')"
    )
