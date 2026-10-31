"""An approved service's upload that asks for more is held for a super admin

A service's extension has a second stage again: `held`, beside `live`.
Approval covers the grants the super admin saw; a later version that
asks for more of them — a host, a path, a secret, a tool, a hook, a
prompt section — waits as `held` while `live` keeps reaching machines,
until a super admin approves or rejects it. See
`docs/service-identity.md` §8.1 and `app.services.identity.grants`.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-31

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0025'
down_revision: Union[str, Sequence[str], None] = '0024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'service_extensions',
        sa.Column('stage', sa.Text(), server_default=sa.text("'live'"), nullable=False),
    )
    op.drop_constraint('service_extensions_pkey', 'service_extensions', type_='primary')
    op.create_primary_key('service_extensions_pkey', 'service_extensions', ['client_id', 'stage'])
    op.create_check_constraint('ck_service_extensions_stage', 'service_extensions', "stage IN ('live', 'held')")


def downgrade() -> None:
    # A held upload was never approved; going back, it is rejected.
    op.execute("DELETE FROM service_extensions WHERE stage = 'held'")
    op.drop_constraint('ck_service_extensions_stage', 'service_extensions', type_='check')
    op.drop_constraint('service_extensions_pkey', 'service_extensions', type_='primary')
    op.drop_column('service_extensions', 'stage')
    op.create_primary_key('service_extensions_pkey', 'service_extensions', ['client_id'])
