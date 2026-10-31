"""The shared plane

What has to know about every organisation on the deployment: accounts,
organisations and the domains they own, memberships, invitations,
deployment roles, the inference fleet, the registry of each
organisation's own database (`tenants`), and the deployment's audit
trail. Everything that is one organisation's lives in that
organisation's database instead (`alembic_tenant/`).

One revision, squashed from the history before it: there were no
deployments to carry forward.

Two things are written by hand because autogenerate cannot produce them:
the trigger that makes `audit_events` append-only, and the partial index
that allows one pending invitation per address per organisation (see
`_REFLECTION_BLIND_SPOTS` in `alembic/env.py`).

Revision ID: 0001
Revises:
Create Date: 2026-10-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0001'
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
    # The database refuses an UPDATE, rather than the application merely
    # not issuing one. DELETE stays allowed: retention needs it.
    op.execute(_BLOCK_UPDATE_FUNCTION)
    op.execute(
        'CREATE TRIGGER audit_events_no_update '
        'BEFORE UPDATE ON audit_events '
        'FOR EACH ROW EXECUTE FUNCTION audit_events_are_append_only()'
    )
    op.create_table(
        'org_invitations',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('token', sa.Text(), nullable=False),
        sa.Column('invited_by', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )
    op.create_index('idx_org_invitations_email', 'org_invitations', ['email'], unique=False)
    op.create_index('idx_org_invitations_org_email', 'org_invitations', ['org_id', 'email'], unique=True)
    op.create_index('idx_org_invitations_token', 'org_invitations', ['token'], unique=True)
    op.create_table(
        'org_memberships',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('invited_email', sa.Text(), nullable=True),
        sa.Column('invited_by', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_org_memberships_org', 'org_memberships', ['org_id'], unique=False)
    op.create_index('idx_org_memberships_org_status', 'org_memberships', ['org_id', 'status'], unique=False)
    op.create_index(
        'uq_org_memberships_pending_invite',
        'org_memberships',
        ['org_id', 'invited_email'],
        unique=True,
        postgresql_where=sa.text("status = 'invited' AND invited_email IS NOT NULL"),
    )
    op.create_index('idx_org_memberships_unique', 'org_memberships', ['org_id', 'user_id'], unique=True)
    op.create_index('idx_org_memberships_user', 'org_memberships', ['user_id'], unique=False)
    op.create_table(
        'organizations',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('display_name', sa.Text(), nullable=False),
        sa.Column('owner_user_id', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )
    op.create_index('idx_organizations_owner', 'organizations', ['owner_user_id'], unique=False)
    op.create_index('idx_organizations_slug', 'organizations', ['slug'], unique=True)
    op.create_table(
        'tenants',
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('database', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('org_id'),
    )
    op.create_index('idx_tenants_database', 'tenants', ['database'], unique=True)
    op.create_index('idx_tenants_number', 'tenants', ['number'], unique=True)
    op.create_table(
        'user_roles',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), nullable=True),
        sa.Column('pending_email', sa.Text(), nullable=True),
        sa.Column('email_verification_token', sa.Text(), nullable=True),
        sa.Column('full_name', sa.Text(), nullable=True),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('assigned_by', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index('idx_user_roles_user', 'user_roles', ['user_id'], unique=True)
    op.create_table(
        'users',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('hashed_password', sa.Text(), nullable=False),
        sa.Column('full_name', sa.Text(), nullable=True),
        sa.Column('auth_provider', sa.Text(), server_default='email', nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('last_sign_in_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('tokens_valid_after', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('deactivated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('is_super_admin', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index('idx_users_email', 'users', ['email'], unique=True)
    op.create_index('idx_users_last_sign_in_at', 'users', ['last_sign_in_at'], unique=False)
    op.create_table(
        'inference_nodes',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('node_id', sa.Text(), nullable=False),
        sa.Column('org_id', sa.Text(), nullable=True),
        sa.Column('base_url', sa.Text(), nullable=False),
        sa.Column('models', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('api_key', sa.Text(), nullable=True),
        sa.Column('max_concurrent', sa.Integer(), server_default=sa.text('64'), nullable=False),
        sa.Column('pools', sa.Text(), server_default=sa.text("'chat'"), nullable=False),
        sa.Column('multimodal', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_by_user_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('node_id', name='uq_inference_nodes_node_id'),
    )
    op.create_index('idx_inference_nodes_org', 'inference_nodes', ['org_id'], unique=False)
    op.create_table(
        'org_domains',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('domain', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_org_domains_domain', 'org_domains', ['domain'], unique=True)
    op.create_index('idx_org_domains_org', 'org_domains', ['org_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_org_domains_org', table_name='org_domains')
    op.drop_index('idx_org_domains_domain', table_name='org_domains')
    op.drop_table('org_domains')
    op.drop_index('idx_inference_nodes_org', table_name='inference_nodes')
    op.drop_table('inference_nodes')
    op.drop_index('idx_users_last_sign_in_at', table_name='users')
    op.drop_index('idx_users_email', table_name='users')
    op.drop_table('users')
    op.drop_index('idx_user_roles_user', table_name='user_roles')
    op.drop_table('user_roles')
    op.drop_index('idx_tenants_number', table_name='tenants')
    op.drop_index('idx_tenants_database', table_name='tenants')
    op.drop_table('tenants')
    op.drop_index('idx_organizations_slug', table_name='organizations')
    op.drop_index('idx_organizations_owner', table_name='organizations')
    op.drop_table('organizations')
    op.drop_index('idx_org_memberships_user', table_name='org_memberships')
    op.drop_index('idx_org_memberships_unique', table_name='org_memberships')
    op.drop_index('idx_org_memberships_org_status', table_name='org_memberships')
    op.drop_index('idx_org_memberships_org', table_name='org_memberships')
    op.drop_table('org_memberships')
    op.drop_index('idx_org_invitations_token', table_name='org_invitations')
    op.drop_index('idx_org_invitations_org_email', table_name='org_invitations')
    op.drop_index('idx_org_invitations_email', table_name='org_invitations')
    op.drop_table('org_invitations')
    op.drop_index('idx_audit_events_target', table_name='audit_events')
    op.drop_index('idx_audit_events_org', table_name='audit_events')
    op.drop_index('idx_audit_events_occurred_at', table_name='audit_events')
    op.drop_index('idx_audit_events_actor_email', table_name='audit_events')
    op.drop_index('idx_audit_events_actor', table_name='audit_events')
    op.execute('DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events')
    op.drop_table('audit_events')
    op.execute('DROP FUNCTION IF EXISTS audit_events_are_append_only()')
