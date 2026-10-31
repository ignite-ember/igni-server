"""The producer feed is marked as changes happen, and forgets producers the gateway saw go

Every change on the shared plane that can change what a producer is —
a membership, an account's deactivation or deletion, an app's
registration, the caps, an organisation's deletion — marks the producers
it touches in `producer_feed_marks`, by trigger, in the transaction that
made it, so no code path can forget to. The feed numbers what the marks
name when it is next read. A row the gateway has seen inactive is
deleted `PRODUCER_FEED_RETENTION_DAYS` later (`seen_inactive_at`), or at
once after an erasure (`erased`). See `docs/service-identity.md` §11.3.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0014'
down_revision: Union[str, Sequence[str], None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FUNCTIONS = """
CREATE FUNCTION producer_feed_mark(o text, k text, p text) RETURNS void LANGUAGE sql AS $$
    INSERT INTO producer_feed_marks (org_id, producer_kind, producer_id, n) VALUES (o, k, p, 1)
    ON CONFLICT (org_id, producer_kind, producer_id) DO UPDATE SET n = producer_feed_marks.n + 1
$$;

CREATE FUNCTION producer_feed_on_membership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.status <> 'invited' THEN
        PERFORM producer_feed_mark(OLD.org_id, 'person', OLD.user_id);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.status <> 'invited' THEN
        PERFORM producer_feed_mark(NEW.org_id, 'person', NEW.user_id);
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION producer_feed_on_account() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    subject text := CASE WHEN TG_OP = 'DELETE' THEN OLD.user_id ELSE NEW.user_id END;
BEGIN
    PERFORM producer_feed_mark(m.org_id, 'person', m.user_id) FROM org_memberships m WHERE m.user_id = subject;
    -- Also a BEFORE DELETE trigger, where NULL would cancel the deletion.
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION producer_feed_on_app_client() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.kind = 'app' THEN
        PERFORM producer_feed_mark(OLD.org_id, 'app', OLD.client_id);
        PERFORM producer_feed_mark(OLD.org_id, 'space', 'app:' || OLD.client_id);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.kind = 'app' THEN
        PERFORM producer_feed_mark(NEW.org_id, 'app', NEW.client_id);
        PERFORM producer_feed_mark(NEW.org_id, 'space', 'app:' || NEW.client_id);
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION producer_feed_on_caps() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM producer_feed_mark('*', '*', '*');
    RETURN NULL;
END $$;

CREATE FUNCTION producer_feed_on_organisation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM producer_feed_mark(OLD.id, '*', '*');
    RETURN NULL;
END $$;

CREATE TRIGGER producer_feed_on_membership AFTER INSERT OR UPDATE OR DELETE ON org_memberships
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_membership();
CREATE TRIGGER producer_feed_on_account_deactivated AFTER UPDATE OF deactivated_at ON users
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_account();
CREATE TRIGGER producer_feed_on_account_deleted BEFORE DELETE ON users
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_account();
CREATE TRIGGER producer_feed_on_app_client AFTER INSERT OR UPDATE OR DELETE ON registered_clients
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_app_client();
CREATE TRIGGER producer_feed_on_caps AFTER INSERT OR UPDATE OR DELETE ON platform_vm_caps
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_caps();
CREATE TRIGGER producer_feed_on_organisation AFTER DELETE ON organizations
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_organisation();
"""

_DROP = """
DROP TRIGGER producer_feed_on_organisation ON organizations;
DROP TRIGGER producer_feed_on_caps ON platform_vm_caps;
DROP TRIGGER producer_feed_on_app_client ON registered_clients;
DROP TRIGGER producer_feed_on_account_deleted ON users;
DROP TRIGGER producer_feed_on_account_deactivated ON users;
DROP TRIGGER producer_feed_on_membership ON org_memberships;
DROP FUNCTION producer_feed_on_organisation();
DROP FUNCTION producer_feed_on_caps();
DROP FUNCTION producer_feed_on_app_client();
DROP FUNCTION producer_feed_on_account();
DROP FUNCTION producer_feed_on_membership();
DROP FUNCTION producer_feed_mark(text, text, text);
"""


def upgrade() -> None:
    op.create_table(
        'producer_feed_marks',
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('producer_kind', sa.Text(), nullable=False),
        sa.Column('producer_id', sa.Text(), nullable=False),
        sa.Column('n', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
        sa.PrimaryKeyConstraint('org_id', 'producer_kind', 'producer_id', name='pk_producer_feed_marks'),
    )
    op.add_column('producer_feed', sa.Column('seen_inactive_at', sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column(
        'producer_feed', sa.Column('erased', sa.Boolean(), server_default=sa.text('false'), nullable=False)
    )
    op.create_index('idx_producer_feed_org', 'producer_feed', ['org_id', 'producer_kind'])
    op.execute(_FUNCTIONS)


def downgrade() -> None:
    op.execute(_DROP)
    op.drop_index('idx_producer_feed_org', table_name='producer_feed')
    op.drop_column('producer_feed', 'erased')
    op.drop_column('producer_feed', 'seen_inactive_at')
    op.drop_table('producer_feed_marks')
