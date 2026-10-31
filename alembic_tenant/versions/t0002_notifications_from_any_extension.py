"""Notifications from any extension

A notification stops being document-shaped (`docs/service-identity.md`
§10.1): it records which extension it came from (`source`, from the
calling service's upload) and which registration posted it, a free-text
`kind`, a `title` and one-line `body`, a `target` page inside the
extension, and when it was delivered to the person's machine. The
document columns and the fixed vocabulary of kinds go.

Nothing has been delivered on a running deployment yet, so the table is
replaced rather than migrated.

Revision ID: t0002
Revises: t0001
Create Date: 2026-10-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 't0002'
down_revision: Union[str, Sequence[str], None] = 't0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EVENT_ONLY = sa.text('event_key IS NOT NULL')


def _drop() -> None:
    op.drop_index('uq_notifications_event', table_name='notifications', postgresql_where=_EVENT_ONLY)
    op.drop_index('idx_notifications_for_user', table_name='notifications')
    op.drop_table('notifications')


def upgrade() -> None:
    """Upgrade schema."""
    _drop()
    op.create_table(
        'notifications',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('source_client_id', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('actor_user_id', sa.Text(), nullable=True),
        sa.Column('actor_email', sa.Text(), nullable=True),
        sa.Column('target', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('event_key', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('delivered_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('read_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_notifications_for_user', 'notifications', ['user_id', 'read_at', 'created_at'], unique=False)
    op.create_index(
        'uq_notifications_event',
        'notifications',
        ['user_id', 'source', 'event_key'],
        unique=True,
        postgresql_where=_EVENT_ONLY,
    )


def downgrade() -> None:
    """Downgrade schema."""
    _drop()
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
        postgresql_where=_EVENT_ONLY,
    )
