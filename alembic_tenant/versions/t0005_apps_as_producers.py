"""Apps as producers, and the extensions each has on

An app is registered by an org admin (its keys in the shared plane's
`registered_clients`, kind `app`); here is what is the organisation's
own about it: who owns it, and which extensions are on for it. A row
in `org_app_extensions` is on; no row is off, the default — the same
rule as a group's (`docs/service-identity.md` §8.3, §11).

Revision ID: t0005
Revises: t0004
Create Date: 2026-10-26

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0005'
down_revision: Union[str, Sequence[str], None] = 't0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'org_apps',
        sa.Column('app_id', sa.Text(), nullable=False),
        sa.Column('owner_user_id', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('app_id'),
    )
    op.create_table(
        'org_app_extensions',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('app_id', sa.Text(), nullable=False),
        sa.Column('extension_id', sa.Text(), nullable=False),
        sa.Column('enabled_by_user_id', sa.Text(), nullable=True),
        sa.Column('enabled_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['app_id'], ['org_apps.app_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('app_id', 'extension_id', name='uq_org_app_extensions_app_extension'),
    )
    op.create_index('idx_org_app_extensions_extension', 'org_app_extensions', ['extension_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_org_app_extensions_extension', table_name='org_app_extensions')
    op.drop_table('org_app_extensions')
    op.drop_table('org_apps')
