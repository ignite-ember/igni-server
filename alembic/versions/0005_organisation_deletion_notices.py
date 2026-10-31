"""Organisation deletion notices

Deleting an organisation tells every approved service, through an outbox
written in the deletion's own transaction and delivered with retries.
See `docs/service-identity.md` §6.1.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'organisation_deletion_notices',
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('client_id', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['registered_clients.client_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('org_id', 'client_id'),
    )
    op.create_index(
        'idx_organisation_deletion_notices_next_attempt_at',
        'organisation_deletion_notices',
        ['next_attempt_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('idx_organisation_deletion_notices_next_attempt_at', table_name='organisation_deletion_notices')
    op.drop_table('organisation_deletion_notices')
