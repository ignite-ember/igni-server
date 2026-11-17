"""The Default group's description says it is read-only

It told admins to "Edit or delete any of it here", and every edit of the
Default group is refused with 409: it holds what igni ships, and the way
to change it is to create a group from it. Only the line written by
igni is replaced — the description itself cannot be edited, so no admin
text is lost.

Revision ID: t0032
Revises: t0031
Create Date: 2026-11-15

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0032'
down_revision: Union[str, Sequence[str], None] = 't0031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen here rather than imported: a migration says what it did, even
# after the constant moves on.
_BEFORE = (
    'What igni ships, as a starting point. Edit or delete any of it here — '
    'members pick the changes up on their next sync.'
)
_AFTER = (
    'What igni ships, as a starting point. It cannot be changed here: '
    'create a group from it to make your own version.'
)


def _replace(old: str, new: str) -> None:
    op.get_bind().execute(
        sa.text('UPDATE org_groups SET description = :new WHERE is_default AND description = :old'),
        {'new': new, 'old': old},
    )


def upgrade() -> None:
    _replace(_BEFORE, _AFTER)


def downgrade() -> None:
    _replace(_AFTER, _BEFORE)
