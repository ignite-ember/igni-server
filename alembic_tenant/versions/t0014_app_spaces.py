"""An app's spaces: one producer per end user of the app

Each marks the feed when declared or deleted (`docs/service-identity.md`
§17, §11.3). Deleted with their app.

Revision ID: t0014
Revises: t0013
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0014'
down_revision: Union[str, Sequence[str], None] = 't0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FUNCTIONS = """
CREATE FUNCTION producer_feed_on_space() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN PERFORM producer_feed_mark('space', OLD.id); END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN PERFORM producer_feed_mark('space', NEW.id); END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER producer_feed_on_space AFTER INSERT OR UPDATE OR DELETE ON app_spaces
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_space();
"""


def upgrade() -> None:
    op.create_table(
        'app_spaces',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('app_id', sa.Text(), nullable=False),
        sa.Column('external_id', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['app_id'], ['org_apps.app_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('app_id', 'external_id', name='uq_app_spaces_app_external'),
    )
    op.create_index('idx_app_spaces_app', 'app_spaces', ['app_id'])
    op.execute(_FUNCTIONS)


def downgrade() -> None:
    op.execute('DROP TRIGGER producer_feed_on_space ON app_spaces; DROP FUNCTION producer_feed_on_space();')
    op.drop_index('idx_app_spaces_app', table_name='app_spaces')
    op.drop_table('app_spaces')
