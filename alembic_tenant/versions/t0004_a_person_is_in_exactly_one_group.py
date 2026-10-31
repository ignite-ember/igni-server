"""A person is in exactly one group

`org_group_members` already holds one row per person
(`uq_org_group_members_user_one_group`); what changes is that the row is
always there — the Default group until an admin moves them — and that
putting somebody in a group is an upsert on that constraint. The unique
constraint is itself the index on `user_id` every lookup by person uses,
so the plain index beside it is dropped.

Revision ID: t0004
Revises: t0003
Create Date: 2026-10-26

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 't0004'
down_revision: Union[str, Sequence[str], None] = 't0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index('idx_org_group_members_user', table_name='org_group_members')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index('idx_org_group_members_user', 'org_group_members', ['user_id'], unique=False)
