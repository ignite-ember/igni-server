"""Inviting someone twice must not break their signup.

`TeamDB.create_invited_member` mints the primary identity itself:

    user_id=f'invited-{uuid.uuid4().hex[:12]}'

so every invite carries a *fresh* synthetic id and the existing
`UNIQUE (org_id, user_id)` index cannot tell that two rows describe the
same person. The only guard was a `get_member_by_email` pre-check in
`TeamService.invite_member` — check-then-act, with nothing behind it.

Measured against a running server: eight concurrent invites for one
address produced **five** pending rows. Racing a `member` invite against an
`admin` invite left one of each, so an org could hold the same invitee
pending at two privilege levels at once.

It is worse than untidy because claiming on sign-up activated *every*
matching row and wrote the real `user_id` onto each — two rows in one org
is the same `(org_id, user_id)` twice:

    UniqueViolationError: duplicate key value violates unique constraint
    "idx_org_memberships_unique"

The claim fails as a whole, so the invited person finishes signing up with
**no membership**, while the admin's team list still shows them pending.
An admin re-inviting somebody to correct their role is enough to reach it.

Fixed in two places, because both are needed:

* migration `9f2b7c41ad86` adds a partial unique index on
  `(org_id, invited_email) WHERE status = 'invited'`, collapsing existing
  duplicates first — newest kept;
* claiming (`Admission.admit`, as the account is made) still de-duplicates
  in code, because it runs against databases mid-upgrade and against rows
  an older instance may write during a rolling deploy.

Newest wins in both. Re-inviting is an admin restating current intent, and
keeping the higher-privilege row would turn a corrective demotion into a
silent grant.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import select

from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.schemas.membership_errors import InvitationPending
from app.db.sql.schemas.pending_invites import PendingInvites
from app.db.sql.team import TeamDB
from app.db.sql.user import UserDB

#: Any bcrypt hash; nobody signs in with these accounts.
_HASH = '$2b$04$' + 'a' * 53


@pytest.fixture
async def org(db):
    from tests.conftest import create_org, create_user

    await create_user(db, user_id='inviter', email='inviter@example.com')
    o = await create_org(db, owner_user_id='inviter', slug='invite-org', display_name='I', domains=('example.com',))
    await db.flush()
    return str(o.id)


def _pending(org_id: str, email: str, role: str, *, age_seconds: int) -> OrgMembership:
    """A pending row written the way the product writes them."""
    when = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return OrgMembership(
        id=str(uuid.uuid4()),
        org_id=org_id,
        user_id=f'invited-{uuid.uuid4().hex[:12]}',
        invited_email=email,
        role=role,
        status='invited',
        invited_by='inviter',
        created_at=when,
        updated_at=when,
    )


class TestTheConstraintStopsDuplicates:
    async def test_a_second_pending_invite_is_refused(self, db, org):
        team = TeamDB()
        await team.create_invited_member(org_id=org, email='dup@example.com', role='member', invited_by='inviter')

        with pytest.raises(InvitationPending) as caught:
            await team.create_invited_member(org_id=org, email='dup@example.com', role='admin', invited_by='inviter')

        assert caught.value.email == 'dup@example.com'

    async def test_concurrent_invites_produce_one_row(self, db, org):
        """The shape that produced five rows."""
        team = TeamDB()
        results = await asyncio.gather(
            *[
                team.create_invited_member(org_id=org, email='race@example.com', role='member', invited_by='inviter')
                for _ in range(6)
            ],
            return_exceptions=True,
        )

        created = [r for r in results if isinstance(r, OrgMembership)]
        conflicts = [r for r in results if isinstance(r, InvitationPending)]
        other = [r for r in results if not isinstance(r, (OrgMembership, InvitationPending))]

        assert not other, f'unexpected failures: {other}'
        assert len(created) == 1, f'{len(created)} rows created'
        assert len(conflicts) == 5

    async def test_a_different_org_may_still_invite_the_same_person(self, db, org):
        """The index is per org. Scoping it globally would stop somebody
        being invited to two organizations, which is a supported thing."""
        from tests.conftest import create_org

        other = await create_org(db, owner_user_id='inviter', slug='other-org', display_name='O')
        await db.flush()

        team = TeamDB()
        await team.create_invited_member(org_id=org, email='both@example.com', role='member', invited_by='inviter')
        second = await team.create_invited_member(
            org_id=str(other.id), email='both@example.com', role='member', invited_by='inviter'
        )

        assert second is not None

    async def test_a_claimed_invite_does_not_block_a_later_one(self, db, org):
        """Partial on `status = 'invited'`, so somebody who joined, left,
        and is invited back is not refused by a stale row."""
        team = TeamDB()
        await team.create_invited_member(org_id=org, email='returning@example.com', role='member', invited_by='inviter')
        await UserDB().create(email='returning@example.com', hashed_password=_HASH)

        again = await team.create_invited_member(
            org_id=org, email='returning@example.com', role='member', invited_by='inviter'
        )

        assert again is not None


class TestTheDeduplicationRule:
    """`PendingInvites.newest_per_org`, directly.

    The state it guards — two pending rows for one address in one org — can
    no longer be created: the index refuses it. Reproducing it in a test by
    dropping the index **deadlocks**: the DDL takes an exclusive lock while
    the claim opened its own session, and the two block
    each other. That attempt was killed at a 600s timeout.

    So the logic is pure and tested as such, and the claim path is covered
    against real, non-duplicate state below.
    """

    @staticmethod
    def _row(org_id: str, role: str, *, age_seconds: int, ident: str = ''):
        when = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        return OrgMembership(
            id=ident or str(uuid.uuid4()),
            org_id=org_id,
            user_id=f'invited-{uuid.uuid4().hex[:12]}',
            invited_email='x@example.com',
            role=role,
            status='invited',
            created_at=when,
            updated_at=when,
        )

    def test_the_newest_row_is_kept(self):
        older = self._row('org-a', 'admin', age_seconds=600)
        newer = self._row('org-a', 'member', age_seconds=10)

        keep, superseded = PendingInvites([older, newer]).newest_per_org()

        assert list(keep) == ['org-a']
        # The corrective demotion holds, not the earlier admin grant — the
        # whole reason "newest" rather than "highest privilege".
        assert keep['org-a'].role == 'member'
        assert superseded == [older]

    def test_input_order_does_not_matter(self):
        older = self._row('org-a', 'admin', age_seconds=600)
        newer = self._row('org-a', 'member', age_seconds=10)

        for rows in ([older, newer], [newer, older]):
            keep, _ = PendingInvites(rows).newest_per_org()
            assert keep['org-a'].role == 'member', rows

    def test_orgs_are_independent(self):
        """De-duplication is per org: it must not collapse memberships of
        different organizations into one."""
        rows = [
            self._row('org-a', 'admin', age_seconds=600),
            self._row('org-a', 'member', age_seconds=10),
            self._row('org-b', 'member', age_seconds=5),
        ]

        keep, superseded = PendingInvites(rows).newest_per_org()

        assert set(keep) == {'org-a', 'org-b'}
        assert len(superseded) == 1

    def test_a_tie_on_created_at_is_broken_deterministically(self):
        """Equal timestamps are realistic — two rows written in the same
        transaction — and a non-deterministic winner would make the role
        somebody ends up with depend on scan order."""
        a = self._row('org-a', 'admin', age_seconds=60, ident='aaa')
        b = self._row('org-a', 'member', age_seconds=60, ident='bbb')

        first, _ = PendingInvites([a, b]).newest_per_org()
        second, _ = PendingInvites([b, a]).newest_per_org()

        assert first['org-a'].id == second['org-a'].id == 'bbb'

    def test_a_single_row_is_kept_and_nothing_is_superseded(self):
        only = self._row('org-a', 'member', age_seconds=1)

        keep, superseded = PendingInvites([only]).newest_per_org()

        assert keep == {'org-a': only}
        assert superseded == []

    def test_no_rows_is_not_an_error(self):
        assert PendingInvites([]).newest_per_org() == ({}, [])


class TestClaimingRealState:
    """Claiming happens as the account is made (`Admission.admit`)."""

    async def test_the_invitation_to_their_domains_organisation_is_claimed(self, db, org, verify_db):
        """The role the admin chose stands. An invitation to any other
        organisation is not: a person belongs to the organisation owning
        their domain, and to no other."""
        from tests.conftest import create_org

        other = await create_org(db, owner_user_id='inviter', slug='third-org', display_name='T')
        await db.flush()

        team = TeamDB()
        await team.create_invited_member(org_id=org, email='two@example.com', role='admin', invited_by='inviter')
        await team.create_invited_member(
            org_id=str(other.id), email='two@example.com', role='admin', invited_by='inviter'
        )

        user = await UserDB().create(email='two@example.com', hashed_password=_HASH)

        rows = (
            (await verify_db.execute(select(OrgMembership).where(OrgMembership.user_id == str(user.user_id))))
            .scalars()
            .all()
        )
        assert [(r.org_id, r.role, r.status) for r in rows] == [(org, 'admin', 'active')]

    async def test_with_no_invitation_they_join_as_a_member(self, db, org, verify_db):
        user = await UserDB().create(email='nobody@example.com', hashed_password=_HASH)

        rows = (
            (await verify_db.execute(select(OrgMembership).where(OrgMembership.user_id == str(user.user_id))))
            .scalars()
            .all()
        )
        assert [(r.org_id, r.role) for r in rows] == [(org, 'member')]
