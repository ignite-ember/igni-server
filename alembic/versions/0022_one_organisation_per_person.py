"""A person is a member of one organisation at a time, as the database guarantees

Different organisations own different domains and an account's address
decides its organisation; this makes it a constraint rather than a
consequence. Removal deletes the membership, so only a current one —
active or deactivated — counts. See `docs/service-identity.md` §20.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-28

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0022'
down_revision: Union[str, Sequence[str], None] = '0021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'uq_org_memberships_one_org_per_person',
        'org_memberships',
        ['user_id'],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'deactivated')"),
    )


def downgrade() -> None:
    op.drop_index('uq_org_memberships_one_org_per_person', table_name='org_memberships')
