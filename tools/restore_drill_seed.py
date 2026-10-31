"""Put representative rows in the databases the restore drill will dump.

F5. Run *inside the shipped API image*, mounted in, so it uses the
application's own models and moves with the schema. The first version of
this was raw SQL in the drill script and it took four attempts to write:
`audit_events.id` is text with no default and `outcome` is NOT NULL, and
every one of those is a detail that changes with a migration. A fixture
that has to be repaired after every migration is a fixture people delete.

Every organisation has its own database (`app.db.sql.tenancy`), so a
backup is one archive for the shared plane and one per organisation. The
drill needs both kinds to have something in them, and needs the
organisation databases to exist the way the application makes them —
so they are provisioned by the application's own code (`Tenants`), which
copies the template and registers the result, rather than by a
`CREATE DATABASE` here that could drift from it.

What is here, and why each one.

In the shared plane:

* **a user, an organisation, a membership and a domain** — so a foreign
  key has something to hold across the restore;
* **an inference node** — its `api_key` and `models` are the
  byte-for-byte comparison. A restore that mangled encoding would still
  produce the right row count, and a credential that comes back different
  is unrecoverable rather than merely wrong. `models` carries a
  non-ASCII adapter name on purpose, so the comparison covers the text
  encoding and JSONB rather than only the ASCII a token happens to be;
* **two tenant registry rows**, written by provisioning — `tenants.number`
  names an organisation's own database, and its unique index is what
  stops two organisations being handed the same one. The drill checks
  the index came back by trying to break it;
* **two deployment audit events**, one allowed and one denied —
  `audit_events` is the table the backup script's canary check looks
  for, and the table whose append-only trigger the drill re-tests after
  the restore.

In each organisation's own database, with *different* counts so an
archive holding the other organisation's rows would not compare equal:

* **the first organisation (active)**: a group with a member, a
  notification, a usage row and three audit events;
* **the second organisation (detached)**: a group and one audit event,
  then detached — deleted, data kept. Its archive must still be written,
  and the drill checks that it is.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models import load_all_models
from app.db.sql.models.audit_event import AuditEvent, DeploymentAuditEvent
from app.db.sql.models.chat_usage import ChatUsage
from app.db.sql.models.inference_node import InferenceNode
from app.db.sql.models.notification import Notification
from app.db.sql.models.org_domain import OrgDomain
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_group_member import OrgGroupMember
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.models.user import User
from app.db.sql.tenancy import Tenants, TenantSessions

#: Not a real token, but constant, so the drill's md5 comparison is
#: against a value a human can read in a log.
NODE_API_KEY = 'drill-node-token-that-must-survive-a-restore'

#: The organisation that stays, and the one that is deleted. The drill
#: reads both ids from here by name, so they are module constants.
ACTIVE_ORG = '22222222-2222-2222-2222-222222222222'
DETACHED_ORG = '66666666-6666-6666-6666-666666666666'


class DrillSeed:
    """The rows, in the order foreign keys and provisioning need them."""

    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc)

    async def shared_plane(self) -> None:
        async with SQLAlchemySessionManager().session_scope() as session:
            session.add(
                User(
                    id='11111111-1111-1111-1111-111111111111',
                    user_id='drill-user',
                    email='drill@example.invalid',
                    hashed_password='$2b$12$fakehashfakehashfakehashfakehashfakehashfakeha',
                    full_name='Drill Person',
                    created_at=self.now,
                    updated_at=self.now,
                )
            )
            session.add(
                Organization(
                    id=ACTIVE_ORG,
                    slug='drill-org',
                    display_name='Drill Org',
                    owner_user_id='drill-user',
                    created_at=self.now,
                    updated_at=self.now,
                )
            )
            # Flushed in dependency order rather than in one batch. The
            # user and the organisation are foreign key targets for the
            # rows below, and one flush is free to reach the database in
            # another order: "Key (org_id)=(2222…) is not present in table
            # organizations".
            await session.flush()

            session.add(
                OrgMembership(
                    id='33333333-3333-3333-3333-333333333333',
                    org_id=ACTIVE_ORG,
                    user_id='drill-user',
                    role='admin',
                    status='active',
                    created_at=self.now,
                    updated_at=self.now,
                )
            )
            session.add(
                OrgDomain(
                    id='44444444-4444-4444-4444-444444444444',
                    org_id=ACTIVE_ORG,
                    domain='example.invalid',
                    created_at=self.now,
                )
            )
            session.add(
                InferenceNode(
                    id='55555555-5555-5555-5555-555555555555',
                    node_id='drill-node',
                    org_id=ACTIVE_ORG,
                    base_url='http://gpu.example.invalid:8000',
                    models=['drill-base', 'drill-adapter-zürich'],
                    api_key=NODE_API_KEY,
                    created_by_user_id='drill-user',
                    created_at=self.now,
                    updated_at=self.now,
                )
            )
            for index, (route, code, outcome) in enumerate(
                (
                    ('/v1/portal/me/revoke-sessions', 200, 'allowed'),
                    ('/v1/sign-in', 401, 'denied'),
                )
            ):
                session.add(
                    DeploymentAuditEvent(
                        id=f'drill-audit-{index}',
                        method='POST',
                        route=route,
                        status_code=code,
                        outcome=outcome,
                        actor_user_id='drill-user' if outcome == 'allowed' else None,
                        occurred_at=self.now,
                    )
                )

    async def active_organisation(self) -> str:
        """Provisioned the way a first request provisions it, then filled."""
        database = await Tenants().database_of(ACTIVE_ORG)
        async with TenantSessions().session_scope(ACTIVE_ORG) as session:
            session.add(OrgGroup(id='drill-t-group-a', org_id=ACTIVE_ORG, name='Drill group', is_default=True))
            await session.flush()
            session.add(OrgGroupMember(id='drill-t-member-a', group_id='drill-t-group-a', user_id='drill-user'))
            session.add(
                Notification(
                    id='drill-t-note-a',
                    user_id='drill-user',
                    source='igni.drill',
                    source_client_id='drill-client',
                    kind='drill',
                    title='Zürich, restored',
                )
            )
            session.add(
                ChatUsage(
                    id='drill-t-usage-a',
                    user_id='drill-user',
                    org_id=ACTIVE_ORG,
                    model='drill-base',
                    input_tokens=12,
                    output_tokens=34,
                    total_tokens=46,
                )
            )
            for index in range(3):
                session.add(
                    AuditEvent(
                        id=f'drill-org-a-audit-{index}',
                        org_id=ACTIVE_ORG,
                        method='POST',
                        route='/v1/portal/org/groups',
                        status_code=200,
                        outcome='allowed',
                        actor_user_id='drill-user',
                        occurred_at=self.now,
                    )
                )
        return database

    async def detached_organisation(self) -> str:
        """Provisioned, filled, then deleted: detached, its data kept."""
        database = await Tenants().database_of(DETACHED_ORG)
        async with TenantSessions().session_scope(DETACHED_ORG) as session:
            session.add(OrgGroup(id='drill-t-group-b', org_id=DETACHED_ORG, name='Gone group'))
            session.add(
                AuditEvent(
                    id='drill-org-b-audit-0',
                    org_id=DETACHED_ORG,
                    method='DELETE',
                    route='/v1/portal/org',
                    status_code=204,
                    outcome='allowed',
                    actor_user_id='drill-user',
                    occurred_at=self.now,
                )
            )
        await Tenants().detach(DETACHED_ORG)
        return database

    async def run(self) -> None:
        # Every model, not just the ones used below: SQLAlchemy cannot
        # configure a mapper whose related class has not been imported,
        # and which relationships exist is the models' business, not this
        # fixture's. `alembic/env.py` calls the same function for the same
        # reason.
        load_all_models()
        try:
            await self.shared_plane()
            active = await self.active_organisation()
            detached = await self.detached_organisation()
        finally:
            await TenantSessions().dispose()
        print(
            'seeded: shared plane (1 user, 1 org, 1 membership, 1 domain, 1 inference node, 2 audit events); '
            f'{active} (active: 1 group, 1 member, 1 notification, 1 usage, 3 audit events); '
            f'{detached} (detached: 1 group, 1 audit event)'
        )


if __name__ == '__main__':
    asyncio.run(DrillSeed().run())
