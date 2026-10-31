"""What a service sends a producer: events for its extension, messages for its agent, and their files

Events may come back not handled; messages, chats and actions carry a
thread and attachments.

`docs/service-identity.md` §15.

Revision ID: t0022
Revises: t0021
Create Date: 2026-10-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = 't0022'
down_revision: Union[str, Sequence[str], None] = 't0021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATES = "state IN ('pending', 'delivered', 'expired', 'dropped', 'failed')"
STATES_NOW = "state IN ('pending', 'delivered', 'not_handled', 'expired', 'dropped', 'failed')"


def upgrade() -> None:
    op.drop_constraint('ck_producer_events_state', 'producer_events', type_='check')
    op.create_check_constraint('ck_producer_events_state', 'producer_events', STATES_NOW)
    op.add_column('producer_events', sa.Column('not_handled_reason', sa.Text(), nullable=True))
    op.add_column('producer_events', sa.Column('kind', sa.Text(), nullable=False, server_default='event'))
    op.create_check_constraint(
        'ck_producer_events_kind', 'producer_events', "kind IN ('event', 'message', 'chat', 'action')"
    )
    op.add_column('producer_events', sa.Column('thread', sa.Text(), nullable=True))
    op.add_column('producer_events', sa.Column('text', sa.Text(), nullable=True))
    op.add_column('producer_events', sa.Column('title', sa.Text(), nullable=True))
    op.add_column(
        'producer_events', sa.Column('start_run', sa.Boolean(), nullable=False, server_default=sa.text('true'))
    )
    op.add_column('producer_events', sa.Column('action', JSONB(), nullable=True))
    op.add_column(
        'producer_events',
        sa.Column('attachment_ids', JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_table(
        'service_attachments',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('client_id', sa.Text(), nullable=False),
        sa.Column('filename', sa.Text(), nullable=False),
        sa.Column('content_type', sa.Text(), nullable=False),
        sa.Column('size', sa.BigInteger(), nullable=False),
        sa.Column('sha256', sa.Text(), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('event_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_service_attachments_event', 'service_attachments', ['event_id'], unique=False)
    op.create_index('idx_service_attachments_expires', 'service_attachments', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_service_attachments_expires', table_name='service_attachments')
    op.drop_index('idx_service_attachments_event', table_name='service_attachments')
    op.drop_table('service_attachments')
    op.execute("DELETE FROM producer_events WHERE kind <> 'event'")
    for column in ('attachment_ids', 'action', 'start_run', 'title', 'text', 'thread'):
        op.drop_column('producer_events', column)
    op.drop_constraint('ck_producer_events_kind', 'producer_events', type_='check')
    op.drop_column('producer_events', 'kind')
    op.execute("UPDATE producer_events SET state = 'delivered' WHERE state = 'not_handled'")
    op.drop_column('producer_events', 'not_handled_reason')
    op.drop_constraint('ck_producer_events_state', 'producer_events', type_='check')
    op.create_check_constraint('ck_producer_events_state', 'producer_events', STATES)
