"""The group every new user lands in, seeded with the agents igni ships.

Agents used to live only in the igni package: to change what a
team's assistant could do you shipped a CLI release. That is the wrong
place for it in a self-hosted deployment — the admin has a portal, and
the agents are content, not code.

So on first contact each org gets a **Default** group holding a copy of
the bundled agents, and users with no group are put in it. From then on
the server is where agents are edited: an admin can rewrite the review
agent, delete the ones their people never use, or point one at a
different adapter, and every member picks it up on their next sync.

It doubles as the starting template. A new group can be created empty or
as a copy of any existing one, and this is the one that holds what igni
ships — so "the standard set, minus the coding agents" is two steps
rather than a merge rule nobody can picture.

``make refresh-default-seed`` re-copies the seed files from a
checked-out igni, and that reaches *new* orgs only: an existing Default
group is read-only, so it is brought to a new seed by an
``alembic_tenant`` migration that freezes it (t0025 was the first). A
refresh without one fails
``tests/test_an_existing_default_group_is_brought_to_what_igni_ships.py``.
Other groups are the org's and no migration touches them.

Seeding happens lazily — the first time we look up somebody's group —
rather than at org creation, because it has to cover the orgs that
already exist as much as the ones made tomorrow.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.group_summaries import GroupSummariesDB
from app.db.sql.groups import GroupsDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.organizations import OrganizationDB
from app.db.sql.schemas.group import EntryCreate, GroupSettings, GroupSummary
from app.db.sql.team import TeamDB
from app.services.portal.entry_kinds import ALLOWED_CONTENT_TYPE
from app.services.portal.group_refusals import GroupRefusals

logger = logging.getLogger(__name__)

DEFAULT_GROUP_NAME = 'Default'
DEFAULT_GROUP_DESCRIPTION = (
    'What igni ships, as a starting point. Edit or delete any of it here — '
    'members pick the changes up on their next sync.'
)

# Vendored copies of igni's ``bundled_agents`` and ``bundled_skills``,
# kept in this repo rather than read from an installed igni: the server
# has no reason to depend on the CLI package, and a deployment may not
# have it at all. ``make refresh-default-seed`` re-copies both.
#: ``kind -> (directory, glob)``. The glob is per-kind because the seed
#: loop used to hardcode ``*.md``, so adding a directory of ``.sh``
#: scripts to it would have silently shipped nothing.
_SEED_DIRS: dict[str, tuple[Path, str]] = {
    'agents': (Path(__file__).parent / 'default_group_agents', '*.md'),
    'skills': (Path(__file__).parent / 'default_group_skills', '*.md'),
    'scripts': (Path(__file__).parent / 'default_group_scripts', '*.sh'),
}

# The two hooks igni provisions into every project.
#
# The command reaches the script through ``{group_scripts}``, which the
# CLI expands to wherever it materialised the group's ``scripts``
# entries. igni ships nothing itself: a hook that pointed at
# ``.igni/hooks/x.sh`` depended on a file the group did not send, and
# on a machine without it bash exits 127 — which the hook runner treats
# as non-blocking, so it became a silent no-op on every matching tool
# call. Shipping the script alongside the declaration removes that
# state entirely.
#
# The client stops registering these itself once a group ships them, so
# they fire once rather than twice.
_SEED_HOOKS: tuple[tuple[str, dict], ...] = (
    (
        'pre-pr-review',
        {
            'event': 'PreToolUse',
            'type': 'command',
            'command': '"{group_scripts}/pre-pr-review.sh"',
            'matcher': 'Bash',
            'timeout': 15000,
        },
    ),
    (
        'post-commit-todo',
        {
            'event': 'PostToolUse',
            'type': 'command',
            'command': '"{group_scripts}/post-commit-todo.sh"',
            'matcher': 'Bash',
            'timeout': 15000,
            'background': True,
        },
    ),
)


def seed_entries() -> list[tuple[str, str, str]]:
    """``(kind, entry_name, content)`` for everything igni ships.

    Agents, the skills behind ``/commit`` and friends, and the two
    hooks igni provisions. A Default group holding only agents was
    telling people that agents are all igni comes with.

    The file stem is the entry name, so a group can still carry an
    ``<agent>.<variant>.md`` of its own: igni writes ``<entry_name>.md``
    and picks the variant while the extension that declared it runs.
    What igni ships here are the plain agents; an extension's versions
    of them come with the extension.
    """
    out: list[tuple[str, str, str]] = []
    for kind, (directory, pattern) in _SEED_DIRS.items():
        if not directory.is_dir():
            logger.warning('No seed %s at %s', kind, directory)
            continue
        found = sorted(directory.glob(pattern))
        if not found:
            logger.warning('Seed directory %s holds no %s', directory, pattern)
        out.extend((kind, path.stem, path.read_text(encoding='utf-8')) for path in found)
    out.extend(('hooks', name, json.dumps(declaration, indent=2)) for name, declaration in _SEED_HOOKS)
    return out


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
    ) -> None:
        self._groups = groups or GroupsDB()
        self._members = members or GroupMembersDB()
        self._entries = entries or GroupEntriesDB()
        self._summaries = summaries or GroupSummariesDB()
        self._orgs = orgs or OrganizationDB()
        self._team = team or TeamDB()

    async def ensure_for_user(self, user_id: str, org_id: str | None = None) -> GroupSummary | None:
        """Return the user's group summary, joining the default if none.

        The annotation said ``dict | None`` and had since before the DB
        layer started answering in models. Every caller already read it
        with attribute access — the only thing the stale annotation
        achieved was hiding whether they were right to.

        `org_id`, when given, is the organisation the person's session is
        in: their group there, or that organisation's default — never a
        group in another organisation they also belong to.

        Never raises. This runs on the path that answers "what is my
        group" for a CLI that is starting up, and a user who cannot be
        auto-assigned should get the behaviour they had before — no
        group — rather than a failed launch.
        """
        try:
            # The *active* variant, deliberately. B8: this branch is the
            # one almost everybody takes — an ordinary member is already
            # in a group — and unfiltered it kept serving the pack to
            # somebody who had been deactivated, while the branch below
            # (`list_user_orgs`) has always filtered on active. The strict
            # check was on the rare path.
            existing = await self._summaries.get_active_user_group_summary(user_id, org_id)
            if existing:
                return existing

            orgs = await self._orgs.list_user_orgs(user_id)
            if org_id is not None:
                # The organisation the session is in, and only if the person
                # is an active member of it (`list_user_orgs` is active-only).
                orgs = [entry for entry in orgs if str(entry[0].id) == str(org_id)]
            if not orgs:
                return None
            org_id = str(orgs[0][0].id)

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
        active = [str(member.user_id) for member in members if member.status == 'active']
        placed = await self._members.place_if_ungrouped(
            org_id, group_id=str(group.id), user_ids=active, added_by_user_id=by_user_id
        )
        if placed:
            logger.info('Put %d people in the default group of org %s', placed, org_id)
        return group

    async def ensure_for_org(self, org_id: str, *, created_by_user_id: str) -> OrgGroup:
        """Return the org's default group, creating and seeding it once.

        Found by its ``is_default`` flag, not its name. An admin who
        renames it has adopted it, and matching on "Default" would hand
        them a fresh one the next time anything asked — which, now that
        listing the groups seeds it, would be on every page load.
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
        for kind, entry_name, content in seed_entries():
            try:
                await self._entries.create_entry(
                    org_id,
                    group_id=str(group.id),
                    entry=EntryCreate(
                        kind=kind,
                        entry_name=entry_name,
                        content=content,
                        content_type=ALLOWED_CONTENT_TYPE[kind],
                        enabled=True,
                    ),
                    by_user_id=created_by_user_id,
                )
                seeded += 1
            except Exception as exc:  # noqa: BLE001 — one bad file is not worth the group
                logger.warning('Could not seed %s %r into org %s: %s', kind, entry_name, org_id, exc)

        logger.info('Seeded the default group of org %s with %d entries', org_id, seeded)
        return group
