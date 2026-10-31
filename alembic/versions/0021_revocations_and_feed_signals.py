"""Revocations a signature-checking party polls, and a signal when the feed has news

A producer made inactive or removed, and a person's sessions withdrawn,
become rows of `revocations`, numbered like the feed. A mark on the
shared plane now also sends `NOTIFY igni_producer_feed`, which wakes the
long polls of the feed and of the revocations (`docs/service-identity.md`
§11.3, §21).

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-28

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0021'
down_revision: Union[str, Sequence[str], None] = '0020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MARK = """
CREATE OR REPLACE FUNCTION producer_feed_mark(o text, k text, p text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO producer_feed_marks (org_id, producer_kind, producer_id, n) VALUES (o, k, p, 1)
    ON CONFLICT (org_id, producer_kind, producer_id) DO UPDATE SET n = producer_feed_marks.n + 1;
    PERFORM pg_notify('igni_producer_feed', o);
END $$;
"""

_MARK_BEFORE = """
CREATE OR REPLACE FUNCTION producer_feed_mark(o text, k text, p text) RETURNS void LANGUAGE sql AS $$
    INSERT INTO producer_feed_marks (org_id, producer_kind, producer_id, n) VALUES (o, k, p, 1)
    ON CONFLICT (org_id, producer_kind, producer_id) DO UPDATE SET n = producer_feed_marks.n + 1
$$;
"""


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence('revocation_seq')))
    op.create_table(
        'revocations',
        sa.Column('seq', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('org_id', sa.Text(), nullable=True),
        sa.Column('subject_id', sa.Text(), nullable=False),
        sa.Column('incarnation', sa.Text(), nullable=True),
        sa.Column('not_before', sa.BigInteger(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('seq', name='pk_revocations'),
    )
    op.create_index('idx_revocations_org', 'revocations', ['org_id', 'seq'])
    op.execute(_MARK)


def downgrade() -> None:
    op.execute(_MARK_BEFORE)
    op.drop_index('idx_revocations_org', table_name='revocations')
    op.drop_table('revocations')
    op.execute(sa.schema.DropSequence(sa.Sequence('revocation_seq')))
