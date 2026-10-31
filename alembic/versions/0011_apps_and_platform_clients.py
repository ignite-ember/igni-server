"""Apps belong to one organisation; platform clients serve them all

An app is a producer an org admin registers on the portal: a registered
client of kind `app` that belongs to exactly one organisation and acts
only as itself. A `platform` client — the cloud's gateway and node
agent — is registered and approved like a service, and acts for no one
organisation. See `docs/service-identity.md` §11.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-26

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('registered_clients', sa.Column('org_id', sa.Text(), nullable=True))
    op.create_foreign_key(
        'registered_clients_org_id_fkey', 'registered_clients', 'organizations', ['org_id'], ['id'], ondelete='CASCADE'
    )
    op.create_index('idx_registered_clients_org', 'registered_clients', ['org_id'])
    op.create_check_constraint(
        'ck_registered_clients_kind', 'registered_clients', "kind IN ('service', 'app', 'platform')"
    )
    op.create_check_constraint(
        'ck_registered_clients_app_has_org', 'registered_clients', "(kind = 'app') = (org_id IS NOT NULL)"
    )


def downgrade() -> None:
    op.drop_constraint('ck_registered_clients_app_has_org', 'registered_clients', type_='check')
    op.drop_constraint('ck_registered_clients_kind', 'registered_clients', type_='check')
    op.drop_index('idx_registered_clients_org', table_name='registered_clients')
    op.drop_constraint('registered_clients_org_id_fkey', 'registered_clients', type_='foreignkey')
    op.drop_column('registered_clients', 'org_id')
