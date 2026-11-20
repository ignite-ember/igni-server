"""Where igni runs for a group, and who pulls each person's events

`org_groups.mode` is `local`, `cloud` or `local_and_cloud`; every group
gets `local_and_cloud`, which is what every group did before. The feed's
group trigger fires on it too, so the gateway hears at once when a group
stops using the cloud.

`person_event_owner` is the person's own choice of which side pulls
their service events, read when their group runs igni in both places
(igni-core `docs/design/local-cloud-switch.md` §6, §7).

Revision ID: t0033
Revises: t0032
Create Date: 2026-11-20

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0033'
down_revision: Union[str, Sequence[str], None] = 't0032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The trigger as t0013 made it, and with `mode` in its column list.
_TRIGGER = """
DROP TRIGGER producer_feed_on_group ON org_groups;
CREATE TRIGGER producer_feed_on_group AFTER INSERT OR DELETE OR UPDATE OF {columns} ON org_groups
    FOR EACH ROW EXECUTE FUNCTION producer_feed_on_group();
"""


def upgrade() -> None:
    op.add_column(
        'org_groups', sa.Column('mode', sa.Text(), nullable=False, server_default=sa.text("'local_and_cloud'"))
    )
    op.create_check_constraint('ck_org_groups_mode', 'org_groups', "mode IN ('local', 'cloud', 'local_and_cloud')")
    op.execute(_TRIGGER.format(columns='vm_profile_id, is_default, mode'))
    op.create_table(
        'person_event_owner',
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('owner', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("owner IN ('local', 'cloud')", name='ck_person_event_owner_owner'),
        sa.PrimaryKeyConstraint('org_id', 'user_id', name='pk_person_event_owner'),
    )


def downgrade() -> None:
    op.drop_table('person_event_owner')
    op.execute(_TRIGGER.format(columns='vm_profile_id, is_default'))
    op.drop_constraint('ck_org_groups_mode', 'org_groups', type_='check')
    op.drop_column('org_groups', 'mode')
