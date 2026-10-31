"""Extension assets

The files an extension runs that are not Python — a JVM, a database, a
model — uploaded beside its bundle, one per digest. See
`docs/service-identity.md` §8.2.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'extension_assets',
        sa.Column('client_id', sa.Text(), nullable=False),
        sa.Column('sha256', sa.Text(), nullable=False),
        sa.Column('size', sa.BigInteger(), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('signature', sa.Text(), nullable=False),
        sa.Column('uploaded_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['registered_clients.client_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('client_id', 'sha256'),
    )


def downgrade() -> None:
    op.drop_table('extension_assets')
