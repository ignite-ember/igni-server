"""Discarding apps and removed members, legal holds on apps, and erasure after inactivity

* `platform_vm_caps.max_erase_after_idle_days` (null: any, never
  included): the longest a profile may let an app's VM go unwoken before
  its state and volume are erased (§12.2).
* `producer_feed.legal_hold`: a hold stands on the producer (a person, or
  an app and its spaces), so the gateway erases nothing of it for
  inactivity; `erasure_done_at`: when the gateway reported a removed
  incarnation's erasure done; `discarded_label`, `discarded_by_user_id`,
  `discarded_at`: an org admin discarded it (§18.5).
* `app_legal_holds`: a hold on an app keeps its VM and its spaces' from
  being erased, discarded or not (§18.2).

Every producer is marked, so the next read of the feed lists each with
its hold. `docs/service-identity.md`.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-01

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0028'
down_revision: Union[str, Sequence[str], None] = '0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('platform_vm_caps', sa.Column('max_erase_after_idle_days', sa.Integer(), nullable=True))
    op.add_column(
        'producer_feed', sa.Column('legal_hold', sa.Boolean(), server_default=sa.text('false'), nullable=False)
    )
    op.add_column('producer_feed', sa.Column('erasure_done_at', sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column('producer_feed', sa.Column('discarded_label', sa.Text(), nullable=True))
    op.add_column('producer_feed', sa.Column('discarded_by_user_id', sa.Text(), nullable=True))
    op.add_column('producer_feed', sa.Column('discarded_at', sa.TIMESTAMP(timezone=True), nullable=True))
    op.create_table(
        'app_legal_holds',
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('app_id', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('placed_by_user_id', sa.Text(), nullable=False),
        sa.Column('placed_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('org_id', 'app_id', name='pk_app_legal_holds'),
    )
    op.execute("SELECT producer_feed_mark('*', '*', '*')")


def downgrade() -> None:
    op.drop_table('app_legal_holds')
    op.drop_column('producer_feed', 'discarded_at')
    op.drop_column('producer_feed', 'discarded_by_user_id')
    op.drop_column('producer_feed', 'discarded_label')
    op.drop_column('producer_feed', 'erasure_done_at')
    op.drop_column('producer_feed', 'legal_hold')
    op.drop_column('platform_vm_caps', 'max_erase_after_idle_days')
