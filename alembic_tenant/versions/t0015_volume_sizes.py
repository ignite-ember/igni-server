"""Volumes: a profile's size and maximum, and one producer's volume grown by an admin

Growing a producer's volume marks it for the feed (`docs/service-identity.md`
§12.1, §11.3).

Revision ID: t0015
Revises: t0014
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0015'
down_revision: Union[str, Sequence[str], None] = 't0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FUNCTIONS = """
CREATE FUNCTION producer_feed_on_volume() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN PERFORM producer_feed_mark(OLD.producer_kind, OLD.producer_id); END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN PERFORM producer_feed_mark(NEW.producer_kind, NEW.producer_id); END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER producer_feed_on_volume AFTER INSERT OR UPDATE OR DELETE ON producer_volumes
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_volume();
"""


def upgrade() -> None:
    op.add_column('vm_profiles', sa.Column('volume_gib', sa.Integer(), server_default=sa.text('10'), nullable=False))
    op.add_column(
        'vm_profiles', sa.Column('volume_max_gib', sa.Integer(), server_default=sa.text('50'), nullable=False)
    )
    op.create_table(
        'producer_volumes',
        sa.Column('producer_kind', sa.Text(), nullable=False),
        sa.Column('producer_id', sa.Text(), nullable=False),
        sa.Column('incarnation', sa.Text(), nullable=False),
        sa.Column('gib', sa.Integer(), nullable=False),
        sa.Column('grown_by_kind', sa.Text(), nullable=False),
        sa.Column('grown_by_id', sa.Text(), nullable=False),
        sa.Column('grown_as', sa.Text(), nullable=False),
        sa.Column('grown_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('producer_kind', 'producer_id', name='pk_producer_volumes'),
    )
    op.execute(_FUNCTIONS)


def downgrade() -> None:
    op.execute('DROP TRIGGER producer_feed_on_volume ON producer_volumes; DROP FUNCTION producer_feed_on_volume();')
    op.drop_table('producer_volumes')
    op.drop_column('vm_profiles', 'volume_max_gib')
    op.drop_column('vm_profiles', 'volume_gib')
