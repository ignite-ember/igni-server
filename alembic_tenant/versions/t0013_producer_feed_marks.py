"""An organisation's own changes to what its producers are mark them for the feed

A group move, a group's or app's profile, a profile itself, an app, and
an app's VM credential mark the producers they touch in this database's
`producer_feed_marks`, by trigger, in the transaction that made them;
the feed on the shared plane reads and numbers them
(`docs/service-identity.md` §11.3).

Revision ID: t0013
Revises: t0012
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0013'
down_revision: Union[str, Sequence[str], None] = 't0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FUNCTIONS = """
CREATE FUNCTION producer_feed_mark(k text, p text) RETURNS void LANGUAGE sql AS $$
    INSERT INTO producer_feed_marks (producer_kind, producer_id, n) VALUES (k, p, 1)
    ON CONFLICT (producer_kind, producer_id) DO UPDATE SET n = producer_feed_marks.n + 1
$$;

CREATE FUNCTION producer_feed_on_group_member() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN PERFORM producer_feed_mark('person', OLD.user_id); END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN PERFORM producer_feed_mark('person', NEW.user_id); END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION producer_feed_on_group() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM producer_feed_mark('person', '*');
    RETURN NULL;
END $$;

CREATE FUNCTION producer_feed_on_profile() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM producer_feed_mark('*', '*');
    RETURN NULL;
END $$;

CREATE FUNCTION producer_feed_on_app() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    app text := CASE WHEN TG_OP = 'DELETE' THEN OLD.app_id ELSE NEW.app_id END;
BEGIN
    PERFORM producer_feed_mark('app', app);
    PERFORM producer_feed_mark('space', 'app:' || app);
    RETURN NULL;
END $$;

CREATE FUNCTION producer_feed_on_credential() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.producer_kind <> 'person' THEN
        PERFORM producer_feed_mark(NEW.producer_kind, NEW.producer_id);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER producer_feed_on_group_member AFTER INSERT OR UPDATE OR DELETE ON org_group_members
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_group_member();
CREATE TRIGGER producer_feed_on_group AFTER INSERT OR DELETE OR UPDATE OF vm_profile_id, is_default ON org_groups
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_group();
CREATE TRIGGER producer_feed_on_profile AFTER INSERT OR UPDATE OR DELETE ON vm_profiles
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_profile();
CREATE TRIGGER producer_feed_on_app AFTER INSERT OR UPDATE OR DELETE ON org_apps
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_app();
CREATE TRIGGER producer_feed_on_credential AFTER INSERT OR UPDATE OF revoked_at ON vm_credentials
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_credential();
"""

_DROP = """
DROP TRIGGER producer_feed_on_credential ON vm_credentials;
DROP TRIGGER producer_feed_on_app ON org_apps;
DROP TRIGGER producer_feed_on_profile ON vm_profiles;
DROP TRIGGER producer_feed_on_group ON org_groups;
DROP TRIGGER producer_feed_on_group_member ON org_group_members;
DROP FUNCTION producer_feed_on_credential();
DROP FUNCTION producer_feed_on_app();
DROP FUNCTION producer_feed_on_profile();
DROP FUNCTION producer_feed_on_group();
DROP FUNCTION producer_feed_on_group_member();
DROP FUNCTION producer_feed_mark(text, text);
"""


def upgrade() -> None:
    op.create_table(
        'producer_feed_marks',
        sa.Column('producer_kind', sa.Text(), nullable=False),
        sa.Column('producer_id', sa.Text(), nullable=False),
        sa.Column('n', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
        sa.PrimaryKeyConstraint('producer_kind', 'producer_id', name='pk_producer_feed_marks'),
    )
    op.execute(_FUNCTIONS)


def downgrade() -> None:
    op.execute(_DROP)
    op.drop_table('producer_feed_marks')
