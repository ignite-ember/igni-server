"""Commands in producers' VMs, jobs for many, and their audit log

`docs/service-identity.md` §13.

Revision ID: t0007
Revises: t0006
Create Date: 2026-10-26

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0007'
down_revision: Union[str, Sequence[str], None] = 't0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vm_jobs',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('scope_kind', sa.Text(), nullable=False),
        sa.Column('scope_id', sa.Text(), nullable=True),
        sa.Column('command', sa.Text(), nullable=False),
        sa.Column('timeout_s', sa.Integer(), nullable=False),
        sa.Column('requested_by_kind', sa.Text(), nullable=False),
        sa.Column('requested_by_id', sa.Text(), nullable=False),
        sa.Column('requested_as', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('cancelled_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('cancelled_by_id', sa.Text(), nullable=True),
        sa.CheckConstraint("scope_kind IN ('group', 'profile', 'org', 'platform')", name='ck_vm_jobs_scope_kind'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_vm_jobs_active', 'vm_jobs', ['cancelled_at'], unique=False)
    op.create_table(
        'vm_commands',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('producer_kind', sa.Text(), nullable=False),
        sa.Column('producer_id', sa.Text(), nullable=False),
        sa.Column('command', sa.Text(), nullable=False),
        sa.Column('timeout_s', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Text(), nullable=True),
        sa.Column('wake', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('requested_by_kind', sa.Text(), nullable=False),
        sa.Column('requested_by_id', sa.Text(), nullable=False),
        sa.Column('requested_as', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('handed_to', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('handed_out_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('started_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('finished_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('exit_code', sa.Integer(), nullable=True),
        sa.Column('output', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued', 'handed_out', 'running', 'finished', 'cancelled')", name='ck_vm_commands_state'
        ),
        sa.ForeignKeyConstraint(['job_id'], ['vm_jobs.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_vm_commands_producer', 'vm_commands', ['producer_kind', 'producer_id', 'state'], unique=False)
    op.create_index('idx_vm_commands_job', 'vm_commands', ['job_id', 'producer_kind', 'producer_id'], unique=True)
    op.create_index('idx_vm_commands_created', 'vm_commands', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_vm_commands_created', table_name='vm_commands')
    op.drop_index('idx_vm_commands_job', table_name='vm_commands')
    op.drop_index('idx_vm_commands_producer', table_name='vm_commands')
    op.drop_table('vm_commands')
    op.drop_index('idx_vm_jobs_active', table_name='vm_jobs')
    op.drop_table('vm_jobs')
