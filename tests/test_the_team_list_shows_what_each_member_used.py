"""The team list shows each member's requests, tokens and last activity.

It showed "—" for everyone however much they had used: the three numbers
were written as constants while `chat_usage` held the rows. They are
read from the organisation's own usage now — requests and tokens over
the last 30 days, the newest request over all time — in one query for
the whole list.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.db.sql.models.chat_usage import ChatUsage
from app.services.portal.team_service import TeamService
from tests.conftest import add_member, auth_header, create_org, create_user, make_token, put_in_org


def _used(org_id: str, user_id: str, *, tokens: int, at: datetime) -> ChatUsage:
    return ChatUsage(
        user_id=user_id,
        org_id=org_id,
        model='m',
        input_tokens=tokens // 2,
        output_tokens=tokens - tokens // 2,
        total_tokens=tokens,
        stream=0,
        created_at=at,
    )


async def test_each_member_is_shown_what_they_used(client, db):
    now = datetime.now(timezone.utc)
    await create_user(db, user_id='alice', email='alice@team.test')
    await create_user(db, user_id='bob', email='bob@team.test')
    await create_user(db, user_id='carol', email='carol@team.test')
    org = await create_org(db, owner_user_id='alice', slug='team', domains=('team.test',))
    org_id = str(org.id)
    await add_member(db, org_id=org_id, user_id='bob')
    await add_member(db, org_id=org_id, user_id='carol')
    await put_in_org(
        org_id,
        _used(org_id, 'alice', tokens=100, at=now - timedelta(days=2)),
        _used(org_id, 'alice', tokens=50, at=now - timedelta(hours=1)),
        # Outside the window: not counted, but it is when Bob was last active.
        _used(org_id, 'bob', tokens=7000, at=now - timedelta(days=45)),
        # An app's VM is not a member.
        _used(org_id, 'app:reports', tokens=999, at=now),
    )

    resp = await client.get('/portal/team', headers=auth_header(make_token('alice', org_id=org_id)))

    assert resp.status_code == 200
    by_id = {m['user_id']: m for m in resp.json()['members']}
    assert set(by_id) == {'alice', 'bob', 'carol'}
    assert (by_id['alice']['total_requests'], by_id['alice']['total_tokens']) == (2, 150)
    # The field's old name is still sent, for clients that read it.
    assert by_id['alice']['total_sessions'] == 2
    assert datetime.fromisoformat(by_id['alice']['last_active']) == now - timedelta(hours=1)
    assert (by_id['bob']['total_requests'], by_id['bob']['total_tokens']) == (0, 0)
    assert datetime.fromisoformat(by_id['bob']['last_active']) == now - timedelta(days=45)
    assert (by_id['carol']['total_requests'], by_id['carol']['total_tokens'], by_id['carol']['last_active']) == (
        0,
        0,
        None,
    )


async def test_another_organisations_use_is_not_counted(client, db):
    now = datetime.now(timezone.utc)
    await create_user(db, user_id='alice', email='alice@team.test')
    await create_user(db, user_id='zed', email='zed@other.test')
    org = await create_org(db, owner_user_id='alice', slug='team', domains=('team.test',))
    other = await create_org(db, owner_user_id='zed', slug='other', domains=('other.test',))
    await put_in_org(str(other.id), _used(str(other.id), 'zed', tokens=500, at=now))
    # A row filed under the other organisation in this one's database is not this organisation's use.
    await put_in_org(str(org.id), _used(str(other.id), 'alice', tokens=500, at=now))

    resp = await client.get('/portal/team', headers=auth_header(make_token('alice', org_id=str(org.id))))

    assert [
        (m['user_id'], m['total_requests'], m['total_tokens'], m['last_active']) for m in resp.json()['members']
    ] == [('alice', 0, 0, None)]


class _Statements:
    """Counts the SQL statements every engine runs while open."""

    def __init__(self) -> None:
        self.count = 0

    def _seen(self, *_args) -> None:
        self.count += 1

    def __enter__(self) -> _Statements:
        event.listen(Engine, 'before_cursor_execute', self._seen)
        return self

    def __exit__(self, *_exc) -> None:
        event.remove(Engine, 'before_cursor_execute', self._seen)


async def test_the_list_costs_the_same_however_many_members(db):
    """Names and emails come in one query for the whole list.

    They were looked up one member at a time: a 500-person organisation
    cost 500 queries for every load of the Team page.
    """
    await create_user(db, user_id='alice', email='alice@team.test', full_name='Alice')
    org = await create_org(db, owner_user_id='alice', slug='team', domains=('team.test',))
    org_id = str(org.id)

    async def statements_for_the_list() -> int:
        with _Statements() as statements:
            listed = await TeamService().list_members(org_id)
        assert {m.full_name for m in listed.members} >= {'Alice'}
        return statements.count

    await statements_for_the_list()  # warm: the first call opens the engine
    with_one = await statements_for_the_list()
    for n in range(5):
        await create_user(db, user_id=f'm{n}', email=f'm{n}@team.test', full_name=f'Member {n}')
        await add_member(db, org_id=org_id, user_id=f'm{n}')
    with_six = await statements_for_the_list()

    assert with_six == with_one
    listed = await TeamService().list_members(org_id)
    assert {m.user_id: m.full_name for m in listed.members} == {
        'alice': 'Alice',
        **{f'm{n}': f'Member {n}' for n in range(5)},
    }
