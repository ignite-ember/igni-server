"""Extension assets as large objects

An asset is hundreds of megabytes, and a `bytea` column is read and
written whole. Its bytes become a Postgres large object, written and read
in chunks, and the row keeps the object's oid. A trigger unlinks the
object whenever its row goes — the cleanup of assets no version declares,
or the cascade when the service's client is removed — so no object
outlives its row. A plain PL/pgSQL trigger rather than the `lo` contrib
extension's `lo_manage`: an extension is not something every customer's
Postgres is guaranteed to allow, PL/pgSQL is.

No data is carried over: there is nothing to carry.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-25

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import OID

from alembic import op

revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('DELETE FROM extension_assets')
    op.drop_column('extension_assets', 'content')
    op.add_column('extension_assets', sa.Column('content_oid', OID(), nullable=False))
    op.execute("""
        CREATE FUNCTION extension_assets_unlink() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.content_oid IS DISTINCT FROM NEW.content_oid THEN
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
        CREATE TRIGGER extension_assets_unlink
        AFTER DELETE OR UPDATE OF content_oid ON extension_assets
        FOR EACH ROW EXECUTE FUNCTION extension_assets_unlink()
        """)


def downgrade() -> None:
    op.execute('DROP TRIGGER extension_assets_unlink ON extension_assets')
    op.execute('DROP FUNCTION extension_assets_unlink()')
    op.execute('SELECT lo_unlink(content_oid) FROM extension_assets')
    op.execute('DELETE FROM extension_assets')
    op.drop_column('extension_assets', 'content_oid')
    op.add_column('extension_assets', sa.Column('content', sa.LargeBinary(), nullable=False))
