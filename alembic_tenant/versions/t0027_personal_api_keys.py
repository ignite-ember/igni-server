"""Personal API keys: a person's access, handed to a program

One row per key: whose it is, what they called it, a hash of its secret,
when it expires, and why it stopped if it was revoked first. The key is
exchanged at the token endpoint for a short-lived session as the person.
`docs/service-identity.md` §22.

Revision ID: t0027
Revises: t0026
Create Date: 2026-11-06

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0027'
down_revision: Union[str, Sequence[str], None] = 't0026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'api_keys',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('secret_hash', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('revoked_reason', sa.Text(), nullable=True),
        sa.Column('revoked_by_id', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN ('revoked_by_person', 'revoked_by_admin', "
            "'signed_out_or_removed')",
            name='ck_api_keys_revoked_reason',
        ),
    )
    op.create_index(
        'ix_api_keys_user_working',
        'api_keys',
        ['user_id'],
        unique=False,
        postgresql_where=sa.text('revoked_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_api_keys_user_working', table_name='api_keys')
    op.drop_table('api_keys')
