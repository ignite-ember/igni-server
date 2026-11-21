"""The latest time the licence has seen

``licence_clock``: one row, the latest time this deployment has seen,
so a clock wound back past a licence's expiry does not make it valid
again (docs/licence-file.md, "The clock"). Empty until igni server first
starts on this release; then seeded from the newest ``licence:daily``
audit event, when the database's clock could have reached it.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-21

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0032'
down_revision: Union[str, Sequence[str], None] = '0031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'licence_clock',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('seen', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_licence_clock_one_row'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('licence_clock')
