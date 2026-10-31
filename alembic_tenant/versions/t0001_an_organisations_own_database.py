"""An organisation's own database

Every table that belongs to one organisation, in that organisation's own
logical database (`app.db.sql.tenancy`): its groups and what they hold,
its chat usage, notifications, and its audit trail.

Generated from `TenantBase` against an empty database, so it matches the
models exactly. Nothing here references the shared plane by foreign key:
the two are different databases. The trigger that makes `audit_events`
append-only is written by hand, as in the shared plane.

Revision ID: t0001
Revises:
Create Date: 2026-10-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 't0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BLOCK_UPDATE_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_events_are_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'audit_events is append-only: UPDATE is not permitted (attempted on id %)', OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'audit_events',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('occurred_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('actor_user_id', sa.Text(), nullable=True),
        sa.Column('actor_email', sa.Text(), nullable=True),
        sa.Column('org_id', sa.Text(), nullable=True),
        sa.Column('method', sa.Text(), nullable=False),
        sa.Column('route', sa.Text(), nullable=False),
        sa.Column('target_type', sa.Text(), nullable=True),
        sa.Column('target_id', sa.Text(), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('outcome', sa.Text(), nullable=False),
        sa.Column('client_host', sa.Text(), nullable=True),
        sa.Column('forwarded_for', sa.Text(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_audit_events_actor', 'audit_events', ['actor_user_id', sa.literal_column('occurred_at DESC')], unique=False
    )
    op.create_index(
        'idx_audit_events_actor_email',
        'audit_events',
        [sa.literal_column('lower(actor_email)'), sa.literal_column('occurred_at DESC')],
        unique=False,
    )
    op.create_index(
        'idx_audit_events_occurred_at', 'audit_events', [sa.literal_column('occurred_at DESC')], unique=False
    )
    op.create_index(
        'idx_audit_events_org', 'audit_events', ['org_id', sa.literal_column('occurred_at DESC')], unique=False
    )
    op.create_index('idx_audit_events_target', 'audit_events', ['target_type', 'target_id'], unique=False)
    op.execute(_BLOCK_UPDATE_FUNCTION)
    op.execute(
        'CREATE TRIGGER audit_events_no_update '
        'BEFORE UPDATE ON audit_events '
        'FOR EACH ROW EXECUTE FUNCTION audit_events_are_append_only()'
    )
    op.create_table(
        'chat_usage',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.String(length=255), nullable=False),
        sa.Column('org_id', sa.String(length=255), nullable=True),
        sa.Column('model', sa.String(length=255), nullable=False),
        sa.Column('input_tokens', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('output_tokens', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('total_tokens', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('duration', sa.Float(), nullable=True),
        sa.Column('stream', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_chat_usage_org_created', 'chat_usage', ['org_id', 'created_at'], unique=False)
    op.create_index('ix_chat_usage_user_created', 'chat_usage', ['user_id', 'created_at'], unique=False)
    op.create_table(
        'notifications',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('actor_user_id', sa.Text(), nullable=True),
        sa.Column('actor_email', sa.Text(), nullable=True),
        sa.Column('shared_artifact_id', sa.Text(), nullable=True),
        sa.Column('slug', sa.Text(), nullable=True),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('subject_id', sa.Text(), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('event_key', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('read_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_notifications_for_user', 'notifications', ['user_id', 'read_at', 'created_at'], unique=False)
    op.create_index(
        'uq_notifications_event',
        'notifications',
        ['user_id', 'event_key'],
        unique=True,
        postgresql_where=sa.text('event_key IS NOT NULL'),
    )
    op.create_table(
        'org_groups',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('default_model', sa.Text(), nullable=True),
        sa.Column('theme', sa.Text(), nullable=True),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_by_user_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'name', name='uq_org_groups_org_name'),
    )
    op.create_index('idx_org_groups_org', 'org_groups', ['org_id'], unique=False)
    op.create_table(
        'org_group_entries',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('group_id', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('entry_name', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_type', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('source_ref', sa.Text(), nullable=True),
        sa.Column('source_subdir', sa.Text(), nullable=True),
        sa.Column('model', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['org_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'kind', 'entry_name', name='uq_org_group_entries_entry'),
    )
    op.create_index('idx_org_group_entries_group', 'org_group_entries', ['group_id'], unique=False)
    op.create_index('idx_org_group_entries_kind', 'org_group_entries', ['group_id', 'kind'], unique=False)
    op.create_index(
        'idx_org_group_entries_source_url',
        'org_group_entries',
        ['source_url'],
        unique=False,
        postgresql_where=sa.text('source_url IS NOT NULL'),
    )
    op.create_table(
        'org_group_members',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('group_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('added_by_user_id', sa.Text(), nullable=True),
        sa.Column('added_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['org_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_org_group_members_user_one_group'),
    )
    op.create_index('idx_org_group_members_group', 'org_group_members', ['group_id'], unique=False)
    op.create_index('idx_org_group_members_user', 'org_group_members', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_org_group_members_user', table_name='org_group_members')
    op.drop_index('idx_org_group_members_group', table_name='org_group_members')
    op.drop_table('org_group_members')
    op.drop_index(
        'idx_org_group_entries_source_url',
        table_name='org_group_entries',
        postgresql_where=sa.text('source_url IS NOT NULL'),
    )
    op.drop_index('idx_org_group_entries_kind', table_name='org_group_entries')
    op.drop_index('idx_org_group_entries_group', table_name='org_group_entries')
    op.drop_table('org_group_entries')
    op.drop_index('idx_org_groups_org', table_name='org_groups')
    op.drop_table('org_groups')
    op.drop_index(
        'uq_notifications_event', table_name='notifications', postgresql_where=sa.text('event_key IS NOT NULL')
    )
    op.drop_index('idx_notifications_for_user', table_name='notifications')
    op.drop_table('notifications')
    op.drop_index('ix_chat_usage_user_created', table_name='chat_usage')
    op.drop_index('ix_chat_usage_org_created', table_name='chat_usage')
    op.drop_table('chat_usage')
    op.drop_index('idx_audit_events_target', table_name='audit_events')
    op.drop_index('idx_audit_events_org', table_name='audit_events')
    op.drop_index('idx_audit_events_occurred_at', table_name='audit_events')
    op.drop_index('idx_audit_events_actor_email', table_name='audit_events')
    op.drop_index('idx_audit_events_actor', table_name='audit_events')
    op.execute('DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events')
    op.drop_table('audit_events')
    op.execute('DROP FUNCTION IF EXISTS audit_events_are_append_only()')
