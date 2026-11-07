"""Deleting your account, when you have done anything.

``DELETE /portal/me`` raised a foreign-key violation — an unhandled 500 —
for anybody who had created a group, written an entry, added a colleague
to a group or registered an inference node, and then left an org that
survives. Four provenance columns referenced ``users`` with ``NO ACTION``
and were ``NOT NULL``, so the row could not go while they pointed at it.

It hid because of the shape of the test everybody would write first: a
sole member deleting their account takes the whole org with them, which
removes the referencing rows before the user row and passes. Two admins,
one leaves — the normal shape of somebody leaving a company — and it
fails.

The fix is ``SET NULL``, not ``CASCADE``. Cascade would have deleted the
organisation's policy when the person who wrote it closed their account.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import text

from tests.conftest import add_member, auth_header, create_org, create_user, make_token


async def _in_org(org_db, org_id: str, sql: str, params: dict):
    """One query in the organisation's own database, where its groups and usage live."""
    async with org_db(org_id) as session:
        return await session.execute(text(sql), params)


@pytest.fixture
async def leaver_and_stayer(client, db):
    """Two admins in one org. The leaver has done ordinary admin work."""
    await create_user(db, user_id='leaver', email='l@a.com')
    await create_user(db, user_id='stayer', email='s@a.com')
    org = await create_org(db, owner_user_id='leaver', slug='a', display_name='Acme')
    await add_member(db, org_id=str(org.id), user_id='leaver', role='admin')
    await add_member(db, org_id=str(org.id), user_id='stayer', role='admin')

    headers = auth_header(make_token('leaver', org_id=str(org.id)))
    created = await client.post('/portal/groups', json={'name': 'policy'}, headers=headers)
    group_id = created.json()['id']
    await client.post(
        f'/portal/groups/{group_id}/entries',
        json={
            'kind': 'agents',
            'entry_name': 'a1',
            'content': '---\nname: a1\ndescription: d\n---\nbody',
            'content_type': 'markdown',
        },
        headers=headers,
    )
    await client.post(f'/portal/groups/{group_id}/members', json={'user_id': 'stayer'}, headers=headers)
    return {'org': str(org.id), 'group': group_id, 'headers': headers}


class TestALeaverCanActuallyLeave:
    async def test_the_delete_succeeds(self, client, leaver_and_stayer):
        response = await client.delete('/portal/me', headers=leaver_and_stayer['headers'])

        assert response.status_code == 200, response.text

    async def test_the_user_row_is_gone(self, client, db, leaver_and_stayer):
        await client.delete('/portal/me', headers=leaver_and_stayer['headers'])

        count = (await db.execute(text('SELECT count(*) FROM users WHERE user_id = :u'), {'u': 'leaver'})).scalar()
        assert count == 0

    async def test_their_org_membership_is_gone(self, client, db, leaver_and_stayer):
        await client.delete('/portal/me', headers=leaver_and_stayer['headers'])

        count = (
            await db.execute(text('SELECT count(*) FROM org_memberships WHERE user_id = :u'), {'u': 'leaver'})
        ).scalar()
        assert count == 0

    async def test_their_group_membership_is_gone(self, client, org_db, leaver_and_stayer):
        """The membership row goes with the person: that row *is* the
        membership, and one belonging to nobody is not a thing."""
        await client.delete('/portal/me', headers=leaver_and_stayer['headers'])

        count = (
            await _in_org(
                org_db,
                leaver_and_stayer['org'],
                'SELECT count(*) FROM org_group_members WHERE user_id = :u',
                {'u': 'leaver'},
            )
        ).scalar()
        assert count == 0


class TestTheOrgKeepsItsWork:
    """The reason provenance is cleared rather than the rows deleted.

    Groups are in the organisation's own database and `users` in the
    shared plane, so no foreign key joins them; `GroupPersonErasureDB.forget_person`
    does what `SET NULL` used to.
    """

    async def test_the_group_survives(self, client, org_db, leaver_and_stayer):
        await client.delete('/portal/me', headers=leaver_and_stayer['headers'])

        count = (
            await _in_org(
                org_db,
                leaver_and_stayer['org'],
                'SELECT count(*) FROM org_groups WHERE id = :g',
                {'g': leaver_and_stayer['group']},
            )
        ).scalar()
        assert count == 1, 'the organisation lost its policy when an admin left'

    async def test_its_entries_survive(self, client, org_db, leaver_and_stayer):
        await client.delete('/portal/me', headers=leaver_and_stayer['headers'])

        count = (
            await _in_org(
                org_db,
                leaver_and_stayer['org'],
                'SELECT count(*) FROM org_group_entries WHERE group_id = :g',
                {'g': leaver_and_stayer['group']},
            )
        ).scalar()
        assert count == 1

    async def test_the_provenance_is_nulled_not_reassigned(self, client, org_db, leaver_and_stayer):
        """Null rather than somebody else's id. A wrong answer about who
        wrote a policy is worse than no answer."""
        await client.delete('/portal/me', headers=leaver_and_stayer['headers'])

        creator = (
            await _in_org(
                org_db,
                leaver_and_stayer['org'],
                'SELECT created_by_user_id FROM org_groups WHERE id = :g',
                {'g': leaver_and_stayer['group']},
            )
        ).scalar()
        assert creator is None

    async def test_an_entrys_provenance_is_nulled_too(self, client, org_db, leaver_and_stayer):
        await client.delete('/portal/me', headers=leaver_and_stayer['headers'])

        author = (
            await _in_org(
                org_db,
                leaver_and_stayer['org'],
                'SELECT created_by_user_id FROM org_group_entries WHERE group_id = :g',
                {'g': leaver_and_stayer['group']},
            )
        ).scalar()
        assert author is None

    async def test_the_stayers_membership_survives(self, client, org_db, leaver_and_stayer):
        """``added_by_user_id`` pointed at the leaver. The colleague they
        added must not be removed from the group along with them."""
        await client.delete('/portal/me', headers=leaver_and_stayer['headers'])

        rows = (
            await _in_org(
                org_db,
                leaver_and_stayer['org'],
                'SELECT added_by_user_id FROM org_group_members WHERE user_id = :u',
                {'u': 'stayer'},
            )
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] is None


class TestTheSoleMemberCase:
    async def test_a_sole_member_takes_the_org_with_them(self, client, db):
        """The case that passed all along, kept so the fix is shown to be
        about the *other* case rather than about deletion generally."""
        await create_user(db, user_id='solo', email='s@s.com')
        org = await create_org(db, owner_user_id='solo', slug='solo', display_name='Solo')
        await add_member(db, org_id=str(org.id), user_id='solo', role='admin')
        headers = auth_header(make_token('solo', org_id=str(org.id)))
        await client.post('/portal/groups', json={'name': 'mine'}, headers=headers)

        response = await client.delete('/portal/me', headers=headers)

        assert response.status_code == 200, response.text
        count = (
            await db.execute(text('SELECT count(*) FROM organizations WHERE id = :o'), {'o': str(org.id)})
        ).scalar()
        assert count == 0


class TestWhatIsRetained:
    async def test_usage_history_survives_and_that_is_recorded(self, client, org_db, leaver_and_stayer):
        """``chat_usage`` has no foreign key to ``users``, so rows keyed
        to a deleted user id remain.

        Asserted rather than fixed. Billing records normally outlive
        accounts, so retaining them is defensible — but it is a retention
        *decision*, and the docstring on ``delete_account`` used to say
        "permanently delete" without mentioning it. This test exists so
        the decision stays deliberate: anybody who needs deletion to mean
        deletion has to change this test on the way past.
        """
        await _in_org(
            org_db,
            leaver_and_stayer['org'],
            'INSERT INTO chat_usage (id, user_id, org_id, model, input_tokens, output_tokens,'
            ' total_tokens, created_at) VALUES (:i, :u, :o, :m, 1, 1, 2, :t)',
            {
                'i': str(uuid.uuid4()),
                'u': 'leaver',
                'o': leaver_and_stayer['org'],
                'm': 'model-x',
                't': datetime.now(timezone.utc),
            },
        )

        await client.delete('/portal/me', headers=leaver_and_stayer['headers'])

        count = (
            await _in_org(
                org_db, leaver_and_stayer['org'], 'SELECT count(*) FROM chat_usage WHERE user_id = :u', {'u': 'leaver'}
            )
        ).scalar()
        assert count == 1, 'usage history was deleted — intended, but change the docstring too'


class TestTheConstraintsThemselves:
    @pytest.mark.parametrize(
        ('table', 'column'),
        [
            ('inference_nodes', 'created_by_user_id'),
        ],
    )
    async def test_provenance_columns_are_set_null(self, db, table, column):
        """Asserted on the schema for the shared plane, where a foreign
        key to ``users`` can exist. The organisation's own tables cannot
        have one — they are a different database — and are covered by
        the behavioural tests above."""
        rule = (
            await db.execute(
                text(
                    'SELECT rc.delete_rule FROM information_schema.table_constraints tc'
                    ' JOIN information_schema.key_column_usage kcu'
                    '   ON tc.constraint_name = kcu.constraint_name'
                    ' JOIN information_schema.referential_constraints rc'
                    '   ON tc.constraint_name = rc.constraint_name'
                    " WHERE tc.constraint_type = 'FOREIGN KEY'"
                    ' AND tc.table_name = :t AND kcu.column_name = :c'
                ),
                {'t': table, 'c': column},
            )
        ).scalar()

        assert rule == 'SET NULL', f'{table}.{column} is {rule}'


class TestTheDeletionIsAllOrNothing:
    """It used to commit three times.

    `UserService.delete_account` deleted organisations through
    `OrganizationDB`, memberships through `TeamDB`, and the user
    through `UserDB`. Each of those opens its own `session_scope`, so
    each committed independently — and a failure part-way left the
    organisations gone, the memberships gone, and the person still
    able to sign in. That is the worst outcome available on a deletion
    request, because it is the one they will not discover until they
    try.

    One session now. The policy — which orgs go, which are merely
    left, and the refusal when leaving would orphan one — stays in the
    service, because it needs membership roles and the writing does
    not.
    """

    async def test_a_failure_leaves_the_account_whole(self, client, db, monkeypatch):
        """The user row is deleted last, so making *that* statement fail
        is the way to land mid-deletion. Everything before it must roll
        back with it."""
        from app.db.sql.user import UserDB

        user_id = f'u-atomic-{uuid.uuid4().hex[:8]}'
        await create_user(db, user_id=user_id, email=f'{user_id}@example.com')
        org = await create_org(db, owner_user_id=user_id, slug=f'solo-{uuid.uuid4().hex[:6]}', display_name='Solo')
        await db.commit()

        original = UserDB.delete_account

        async def _fail_at_the_end(self, *, user_id, delete_org_ids, leave_org_ids):
            async with self.session_scope() as session:
                from app.db.sql.models.org_membership import OrgMembership
                from app.db.sql.models.organization import Organization

                await session.execute(OrgMembership.__table__.delete().where(OrgMembership.org_id.in_(delete_org_ids)))
                await session.execute(Organization.__table__.delete().where(Organization.id.in_(delete_org_ids)))
                raise RuntimeError('the database went away mid-deletion')

        monkeypatch.setattr(UserDB, 'delete_account', _fail_at_the_end)
        token = make_token(user_id, org_id=str(org.id))
        try:
            with pytest.raises(RuntimeError):
                await client.delete('/portal/me', headers=auth_header(token))
        finally:
            monkeypatch.setattr(UserDB, 'delete_account', original)

        # Nothing is half-done: the org the transaction tried to remove
        # is still there, and so is the person.
        rows = (await db.execute(text('SELECT id FROM organizations WHERE id = :i'), {'i': str(org.id)})).fetchall()
        assert rows, 'the organisation was committed before the failure'
        users = (await db.execute(text('SELECT user_id FROM users WHERE user_id = :u'), {'u': user_id})).fetchall()
        assert users, 'the user was removed despite the deletion failing'

    async def test_the_successful_path_still_removes_everything(self, client, db):
        """The other half: atomicity must not have cost the deletion."""
        user_id = f'u-whole-{uuid.uuid4().hex[:8]}'
        await create_user(db, user_id=user_id, email=f'{user_id}@example.com')
        org = await create_org(db, owner_user_id=user_id, slug=f'gone-{uuid.uuid4().hex[:6]}', display_name='Gone')
        await db.commit()

        token = make_token(user_id, org_id=str(org.id))
        response = await client.delete('/portal/me', headers=auth_header(token))

        assert response.status_code in (200, 204), response.text
        assert not (await db.execute(text('SELECT user_id FROM users WHERE user_id = :u'), {'u': user_id})).fetchall()
        assert not (await db.execute(text('SELECT id FROM organizations WHERE id = :i'), {'i': str(org.id)})).fetchall()
