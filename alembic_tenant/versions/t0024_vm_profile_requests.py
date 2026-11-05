"""VM profile requests: what an app or an extension asks its VMs to get

One per app and one per extension: pending until an org admin approves it
(it becomes a profile) or rejects it. Nothing it asks for runs before.
`docs/service-identity.md` §12.3.

Revision ID: t0024
Revises: t0023
Create Date: 2026-11-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = 't0024'
down_revision: Union[str, Sequence[str], None] = 't0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vm_profile_requests',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('subject_kind', sa.Text(), nullable=False),
        sa.Column('subject_id', sa.Text(), nullable=False),
        sa.Column('settings', JSONB(), nullable=False),
        sa.Column('version', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('requested_by_kind', sa.Text(), nullable=False),
        sa.Column('requested_by_id', sa.Text(), nullable=False),
        sa.Column('requested_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('decided_by_user_id', sa.Text(), nullable=True),
        sa.Column('decided_as', sa.Text(), nullable=True),
        sa.Column('decided_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('profile_id', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['profile_id'], ['vm_profiles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subject_kind', 'subject_id', name='uq_vm_profile_requests_subject'),
        sa.CheckConstraint("subject_kind IN ('app', 'extension')", name='ck_vm_profile_requests_subject_kind'),
        sa.CheckConstraint("state IN ('pending', 'approved', 'rejected')", name='ck_vm_profile_requests_state'),
    )


def downgrade() -> None:
    op.drop_table('vm_profile_requests')
