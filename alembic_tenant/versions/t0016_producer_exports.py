"""Exports of a removed member's data, asked for by an org admin during retention

`docs/service-identity.md` §18.4.

Revision ID: t0016
Revises: t0015
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import OID

from alembic import op

revision: str = 't0016'
down_revision: Union[str, Sequence[str], None] = 't0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'producer_exports',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('producer_kind', sa.Text(), nullable=False),
        sa.Column('producer_id', sa.Text(), nullable=False),
        sa.Column('incarnation', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('requested_by_user_id', sa.Text(), nullable=False),
        sa.Column('requested_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('handed_to', sa.Text(), nullable=True),
        sa.Column('handed_out_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('finished_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('size', sa.BigInteger(), nullable=True),
        sa.Column('content_oid', OID(), nullable=True),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('requested', 'handed_out', 'ready', 'failed')", name='ck_producer_exports_state'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_producer_exports_state', 'producer_exports', ['state'])
    op.execute("""
        CREATE FUNCTION producer_exports_unlink() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.content_oid IS NOT NULL
               AND (TG_OP = 'DELETE' OR OLD.content_oid IS DISTINCT FROM NEW.content_oid) THEN
                PERFORM lo_unlink(OLD.content_oid);
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$
        """)
    op.execute("""
        CREATE TRIGGER producer_exports_unlink
        AFTER DELETE OR UPDATE OF content_oid ON producer_exports
        FOR EACH ROW EXECUTE FUNCTION producer_exports_unlink()
        """)


def downgrade() -> None:
    op.execute('DROP TRIGGER producer_exports_unlink ON producer_exports')
    op.execute('DROP FUNCTION producer_exports_unlink()')
    op.execute('SELECT lo_unlink(content_oid) FROM producer_exports WHERE content_oid IS NOT NULL')
    op.drop_index('idx_producer_exports_state', table_name='producer_exports')
    op.drop_table('producer_exports')
