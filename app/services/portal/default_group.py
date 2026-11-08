"""The group every new user lands in, seeded with what igni ships.

Agents, skills and hooks are content the admin edits in the portal, not
code shipped in a CLI release. Each org gets a **Default** group holding
a copy of the seed (:mod:`app.services.portal.default_group_seed`), and
users with no group are put in it. From then on the server is where they
are edited, and every member picks a change up on their next sync.

It doubles as the starting template: a new group can be created empty or
as a copy of any existing one, and this is the one that holds what igni
ships.

A seed refresh reaches *new* orgs only. An existing Default group is
read-only, so it is brought to a new seed by an ``alembic_tenant``
migration that freezes it; a refresh without one fails
``tests/test_an_existing_default_group_is_brought_to_what_igni_ships.py``.
Other groups are the org's and no migration touches them.

Seeding is lazy — the first time anything looks up somebody's group —
so it covers existing orgs and new ones alike.
"""

from __future__ import annotations

import logging

from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.group_summaries import GroupSummariesDB
from app.db.sql.groups import GroupsDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_membership import ACTIVE
from app.db.sql.organizations import OrganizationDB
from app.db.sql.schemas.group import GroupSettings, GroupSummary
from app.db.sql.team import TeamDB
from app.services.portal.default_group_seed import BUNDLED_SEED, DefaultGroupSeed
from app.services.portal.group_refusals import GroupRefusals

logger = logging.getLogger(__name__)

DEFAULT_GROUP_NAME = 'Default'
DEFAULT_GROUP_DESCRIPTION = (
    'What igni ships, as a starting point. Edit or delete any of it here — '
    'members pick the changes up on their next sync.'
)


class DefaultGroup:
    """Creates the org's default group and puts ungrouped users in it."""

    def __init__(
        self,
        *,
        groups: GroupsDB | None = None,
        members: GroupMembersDB | None = None,
        entries: GroupEntriesDB | None = None,
        summaries: GroupSummariesDB | None = None,
        orgs: OrganizationDB | None = None,
        team: TeamDB | None = None,
        seed: DefaultGroupSeed = BUNDLED_SEED,
    ) -> None:
        self._groups = groups or GroupsDB()
        self._members = members or GroupMembersDB()
        self._entries = entries or GroupEntriesDB()
        self._summaries = summaries or GroupSummariesDB()
        self._orgs = orgs or OrganizationDB()
        self._team = team or TeamDB()
        self._seed = seed

    async def ensure_for_user(self, user_id: str, org_id: str | None = None) -> GroupSummary | None:
        """Return the user's group summary, joining the default if none.

        `org_id`, when given, is the organisation the person's session is
        in: their group there, or that organisation's default — never a
        group in another organisation they also belong to.

        Only an active member gets a group, on either branch: the
        summary lookup is the active-only variant, as is `list_user_orgs`.

        Never raises. This runs on the path that answers "what is my
        group" for a CLI that is starting up, and a user who cannot be
        auto-assigned gets no group rather than a failed launch.
        """
        try:
            existing = await self._summaries.get_active_user_group_summary(user_id, org_id)
            if existing:
                return existing

            org_ids = [str(org.id) for org, _membership in await self._orgs.list_user_orgs(user_id)]
            if org_id is not None:
                org_ids = [candidate for candidate in org_ids if candidate == str(org_id)]
            if not org_ids:
                return None
            org_id = org_ids[0]

            group = await self.ensure_for_org(org_id, created_by_user_id=user_id)

            # Only if still ungrouped: an admin placing them meanwhile wins.
            if await self._members.place_if_ungrouped(
                org_id, group_id=str(group.id), user_ids=[user_id], added_by_user_id=user_id
            ):
                logger.info('Put %s in the default group of org %s', user_id, org_id)
            return await self._summaries.get_active_user_group_summary(user_id, org_id)
        except Exception as exc:  # noqa: BLE001 — a CLI start must survive this
            logger.warning('Could not assign %s to a default group: %s', user_id, exc)
            return None

    async def place_everyone(self, org_id: str, *, by_user_id: str) -> OrgGroup:
        """Put every active member of the organisation who is in no group into Default.

        A person joins an organisation in the shared plane, and their
        group lives in the organisation's own database — two databases,
        no transaction across them. So nobody is placed at the moment
        they join; they are placed the first time anything looks:
        their own first sync, or an admin opening the groups. Either
        way, by the time a group is shown, everybody is in one.
        """
        group = await self.ensure_for_org(org_id, created_by_user_id=by_user_id)
        members, _ = await self._team.list_members(org_id)
        active = [str(member.user_id) for member in members if member.status == ACTIVE]
        placed = await self._members.place_if_ungrouped(
            org_id, group_id=str(group.id), user_ids=active, added_by_user_id=by_user_id
        )
        if placed:
            logger.info('Put %d people in the default group of org %s', placed, org_id)
        return group

    async def ensure_for_org(self, org_id: str, *, created_by_user_id: str) -> OrgGroup:
        """Return the org's default group, creating and seeding it once.

        Found by its ``is_default`` flag, not its name: an admin who
        renames it has adopted it, and must not be handed a fresh one
        the next time the groups are listed.
        """
        for group in (await self._groups.list_groups(org_id)).items:
            if group.is_default:
                return group

        with GroupRefusals():
            group = await self._groups.create_group(
                org_id,
                settings=GroupSettings(name=DEFAULT_GROUP_NAME, description=DEFAULT_GROUP_DESCRIPTION),
                by_user_id=created_by_user_id,
                is_default=True,
            )

        seeded = 0
        for entry in self._seed.entries():
            try:
                await self._entries.create_entry(
                    org_id, group_id=str(group.id), entry=entry.to_entry_create(), by_user_id=created_by_user_id
                )
                seeded += 1
            except Exception as exc:  # noqa: BLE001 — one bad file is not worth the group
                logger.warning('Could not seed %s %r into org %s: %s', entry.kind, entry.entry_name, org_id, exc)

        logger.info('Seeded the default group of org %s with %d entries', org_id, seeded)
        return group
